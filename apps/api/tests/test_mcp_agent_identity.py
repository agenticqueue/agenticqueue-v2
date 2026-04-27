import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
API_BASE_URL = os.environ.get("AQ_TEST_API_URL", "http://127.0.0.1:8000")

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
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM audit_log")
        cursor.execute("DELETE FROM api_keys")
        cursor.execute("DELETE FROM actors")


async def _setup_founder(client: httpx.AsyncClient) -> tuple[UUID, str]:
    response = await client.post("/setup", json={})
    assert response.status_code == 200
    payload = response.json()
    return UUID(str(payload["actor_id"])), str(payload["founder_key"])


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
            WHERE op = 'create_actor'
            ORDER BY ts ASC, id ASC
            """
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
        cursor.execute("SELECT count(*) FROM audit_log")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_mcp_agent_identity_records_claimed_identity_and_rest_remains_null(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = await _setup_founder(async_client)

    await _mcp_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": "mcp-agent-identity",
            "kind": "agent",
            "agent_identity": "claude-opus-4-7",
        },
    )
    rest_response = await async_client.post(
        "/actors",
        headers=_auth_headers(founder_key),
        json={"name": "rest-no-agent-identity", "kind": "agent"},
    )

    assert rest_response.status_code == 200
    rows = _create_actor_audit_rows(conn)
    assert rows["mcp-agent-identity"] == (founder_id, "claude-opus-4-7")
    assert rows["rest-no-agent-identity"] == (founder_id, None)


@pytest.mark.asyncio
async def test_mcp_agent_identity_is_task_scoped_under_concurrency(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = await _setup_founder(async_client)

    await asyncio.gather(
        _mcp_call(
            async_client,
            founder_key,
            "create_actor",
            {
                "name": "mcp-concurrent-a",
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
                "name": "mcp-concurrent-b",
                "kind": "agent",
                "agent_identity": "agent-b",
            },
            request_id=11,
        ),
    )

    rows = _create_actor_audit_rows(conn)
    assert rows["mcp-concurrent-a"] == (founder_id, "agent-a")
    assert rows["mcp-concurrent-b"] == (founder_id, "agent-b")


@pytest.mark.asyncio
async def test_mcp_empty_agent_identity_is_treated_as_null(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    founder_id, founder_key = await _setup_founder(async_client)

    await _mcp_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": "mcp-empty-agent-identity",
            "kind": "agent",
            "agent_identity": "",
        },
    )

    rows = _create_actor_audit_rows(conn)
    assert rows["mcp-empty-agent-identity"] == (founder_id, None)


@pytest.mark.asyncio
async def test_mcp_invalid_agent_identity_is_rejected_without_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _founder_id, founder_key = await _setup_founder(async_client)

    payload = await _mcp_raw_call(
        async_client,
        founder_key,
        "create_actor",
        {
            "name": "mcp-invalid-agent-identity",
            "kind": "agent",
            "agent_identity": "<script>",
        },
    )

    assert _audit_count(conn) == 0
    assert "error" in payload or (
        isinstance(payload.get("result"), dict)
        and payload["result"].get("isError") is True
    )
