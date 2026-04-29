import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_actor_state
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
API_BASE_URL = os.environ.get("AQ_TEST_API_URL", "http://127.0.0.1:8000")
ACTOR_PREFIX = "mcp-agent-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live MCP agent identity tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_cap02_state(connection)
        yield connection
        _truncate_cap02_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=10,
    ) as client:
        yield client


def _truncate_cap02_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_actor_state(conn, actor_name_prefix=ACTOR_PREFIX)


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str,
) -> tuple[UUID, str]:
    api_key = f"aq2_mcp_agent_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (name,),
        )
        row = cursor.fetchone()
        assert row is not None
        actor_id = row[0]
        assert isinstance(actor_id, UUID)

        cursor.execute(
            """
            INSERT INTO api_keys
                (actor_id, name, key_hash, prefix, lookup_id)
            VALUES
                (%s, %s, %s, %s, %s)
            """,
            (
                actor_id,
                f"{ACTOR_PREFIX}key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _mcp_raw_call(
    client: httpx.AsyncClient,
    key: str,
    tool: str,
    arguments: dict[str, object],
    *,
    request_id: int = 1,
) -> dict[str, object]:
    response = await client.post(
        "/mcp",
        headers={
            **_auth_headers(key),
            "Accept": "application/json,text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


async def _mcp_call(
    client: httpx.AsyncClient,
    key: str,
    tool: str,
    arguments: dict[str, object],
    *,
    request_id: int = 1,
) -> dict[str, object]:
    payload = await _mcp_raw_call(
        client,
        key,
        tool,
        arguments,
        request_id=request_id,
    )
    result = payload["result"]
    assert isinstance(result, dict)
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    return structured


def _create_actor_audit_rows(
    conn: Connection[tuple[object, ...]],
) -> dict[str, tuple[UUID, str | None]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT request_payload->>'name',
                   authenticated_actor_id,
                   claimed_actor_identity
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
              AND op = 'create_actor'
            ORDER BY ts ASC, id ASC
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        rows = cursor.fetchall()
    result: dict[str, tuple[UUID, str | None]] = {}
    for name, authenticated_actor_id, claimed_actor_identity in rows:
        assert isinstance(name, str)
        assert isinstance(authenticated_actor_id, UUID)
        assert claimed_actor_identity is None or isinstance(
            claimed_actor_identity,
            str,
        )
        result[name] = (authenticated_actor_id, claimed_actor_identity)
    return result


def _audit_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_mcp_agent_identity_records_claimed_identity_and_rest_remains_null(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = _insert_actor_with_key(
        conn,
        name=f"{ACTOR_PREFIX}founder",
    )

    await _mcp_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": f"{ACTOR_PREFIX}identity",
            "kind": "agent",
            "agent_identity": "claude-opus-4-7",
        },
    )
    rest_response = await async_client.post(
        "/actors",
        headers=_auth_headers(founder_key),
        json={"name": f"{ACTOR_PREFIX}rest-no-identity", "kind": "agent"},
    )

    assert rest_response.status_code == 200
    rows = _create_actor_audit_rows(conn)
    assert rows[f"{ACTOR_PREFIX}identity"] == (founder_id, "claude-opus-4-7")
    assert rows[f"{ACTOR_PREFIX}rest-no-identity"] == (founder_id, None)


@pytest.mark.asyncio
async def test_mcp_agent_identity_is_task_scoped_under_concurrency(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = _insert_actor_with_key(
        conn,
        name=f"{ACTOR_PREFIX}founder",
    )

    await asyncio.gather(
        _mcp_call(
            async_client,
            founder_key,
            "create_actor",
            {
                "name": f"{ACTOR_PREFIX}concurrent-a",
                "kind": "agent",
                "agent_identity": "agent-a",
            },
            request_id=10,
        ),
        _mcp_call(
            async_client,
            founder_key,
            "create_actor",
            {
                "name": f"{ACTOR_PREFIX}concurrent-b",
                "kind": "agent",
                "agent_identity": "agent-b",
            },
            request_id=11,
        ),
    )

    rows = _create_actor_audit_rows(conn)
    assert rows[f"{ACTOR_PREFIX}concurrent-a"] == (founder_id, "agent-a")
    assert rows[f"{ACTOR_PREFIX}concurrent-b"] == (founder_id, "agent-b")


@pytest.mark.asyncio
async def test_mcp_empty_agent_identity_is_treated_as_null(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = _insert_actor_with_key(
        conn,
        name=f"{ACTOR_PREFIX}founder",
    )

    await _mcp_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": f"{ACTOR_PREFIX}empty-identity",
            "kind": "agent",
            "agent_identity": "",
        },
    )

    rows = _create_actor_audit_rows(conn)
    assert rows[f"{ACTOR_PREFIX}empty-identity"] == (founder_id, None)


@pytest.mark.asyncio
async def test_mcp_invalid_agent_identity_is_rejected_without_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _founder_id, founder_key = _insert_actor_with_key(
        conn,
        name=f"{ACTOR_PREFIX}founder",
    )

    payload = await _mcp_raw_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": f"{ACTOR_PREFIX}invalid-identity",
            "kind": "agent",
            "agent_identity": "<script>",
        },
    )

    assert _audit_count(conn) == 0
    assert "error" in payload or (
        isinstance(payload.get("result"), dict)
        and payload["result"].get("isError") is True
    )
