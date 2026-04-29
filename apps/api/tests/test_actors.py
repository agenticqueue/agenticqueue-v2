import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_actor_state
from aq_api.app import app
from aq_api.models import CreateActorResponse, ListActorsResponse, WhoamiResponse
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "actor-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live actor tests",
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    from aq_api._db import engine

    await engine.dispose()


def _truncate_cap02_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_actor_state(conn, actor_name_prefix=ACTOR_PREFIX)


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
    key: str | None = None,
    deactivated: bool = False,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_actor_contract_{uuid.uuid4().hex}"
    deactivated_at = datetime.now(UTC) if deactivated else None
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind, deactivated_at)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (actor_name, kind, deactivated_at),
        )
        actor_row = cursor.fetchone()
        assert actor_row is not None
        actor_id = actor_row[0]
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
                f"actor-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


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


def _latest_audit(conn: Connection[tuple[object, ...]]) -> dict[str, object]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        row = cursor.fetchone()
    assert row is not None
    op, target_kind, target_id, request_payload, response_payload, error_code = row
    return {
        "op": op,
        "target_kind": target_kind,
        "target_id": str(target_id) if target_id is not None else None,
        "request_payload": request_payload,
        "response_payload": response_payload,
        "error_code": error_code,
    }


def _api_key_row(
    conn: Connection[tuple[object, ...]],
    actor_id: UUID,
) -> tuple[UUID, str, str, bytes]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, key_hash, prefix, lookup_id
            FROM api_keys
            WHERE actor_id = %s
            """,
            (actor_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    api_key_id, key_hash, prefix, lookup_id = row
    assert isinstance(api_key_id, UUID)
    assert isinstance(key_hash, str)
    assert isinstance(prefix, str)
    assert isinstance(lookup_id, bytes)
    return api_key_id, key_hash, prefix, lookup_id


@pytest.mark.asyncio
async def test_actor_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'

    whoami = await async_client.get("/actors/me")
    actor_list = await async_client.get("/actors")
    create = await async_client.post(
        "/actors",
        json={"name": "actor-test-worker", "kind": "agent"},
    )

    assert whoami.status_code == 401
    assert whoami.content == expected
    assert actor_list.status_code == 401
    assert actor_list.content == expected
    assert create.status_code == 401
    assert create.content == expected


@pytest.mark.asyncio
async def test_whoami_returns_current_actor_and_reads_do_not_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="actor-test-founder")
    before = _audit_count(conn)

    rest_response = await async_client.get("/actors/me", headers=_auth_headers(key))

    assert rest_response.status_code == 200
    rest_payload = rest_response.json()
    whoami = WhoamiResponse.model_validate(rest_payload)
    assert whoami.actor.id == actor_id
    assert whoami.actor.name == "actor-test-founder"
    assert _audit_count(conn) == before


@pytest.mark.asyncio
async def test_create_actor_mints_key_once_and_writes_redacted_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="actor-test-founder")

    response = await async_client.post(
        "/actors",
        headers=_auth_headers(key),
        json={
            "name": "actor-test-worker",
            "kind": "agent",
            "key_name": "worker default",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    created = CreateActorResponse.model_validate(payload)
    assert created.actor.name == "actor-test-worker"
    assert created.actor.kind == "agent"
    assert payload["key"].startswith("aq2_")
    assert payload["api_key"]["prefix"] == payload["key"][:DISPLAY_PREFIX_LENGTH]

    api_key_id, key_hash, prefix, lookup_id = _api_key_row(conn, created.actor.id)
    assert created.api_key.id == api_key_id
    assert bool(PASSWORD_HASHER.verify(key_hash, payload["key"]))
    assert prefix == payload["key"][:DISPLAY_PREFIX_LENGTH]
    assert lookup_id == lookup_id_for_key(payload["key"])

    audit = _latest_audit(conn)
    assert audit["op"] == "create_actor"
    assert audit["target_kind"] == "actor"
    assert audit["target_id"] == str(created.actor.id)
    assert audit["error_code"] is None
    audit_text = repr(audit)
    assert payload["key"] not in audit_text
    assert "[REDACTED]" in audit_text

    list_response = await async_client.get(
        "/actors",
        headers=_auth_headers(key),
        params={"limit": 200},
    )
    assert list_response.status_code == 200
    listed = ListActorsResponse.model_validate(list_response.json())
    assert created.actor.id in {actor.id for actor in listed.actors}


@pytest.mark.asyncio
async def test_create_actor_rejects_extra_field_without_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="actor-test-founder")
    before = _audit_count(conn)

    response = await async_client.post(
        "/actors",
        headers=_auth_headers(key),
        json={"name": "actor-test-bad", "kind": "agent", "extra_field": True},
    )

    assert response.status_code == 422
    assert _audit_count(conn) == before


@pytest.mark.asyncio
async def test_list_actors_pagination_and_deactivated_filter(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _founder_id, key = _insert_actor_with_key(conn, name="actor-test-founder")
    active_ids = [
        _insert_actor_with_key(conn, name=f"actor-test-active-{index}")[0]
        for index in range(3)
    ]
    deactivated_id = _insert_actor_with_key(
        conn,
        name="actor-test-deactivated",
        deactivated=True,
    )[0]

    first = await async_client.get(
        "/actors",
        headers=_auth_headers(key),
        params={"limit": 2},
    )
    assert first.status_code == 200
    first_page = ListActorsResponse.model_validate(first.json())
    assert len(first_page.actors) == 2
    assert first_page.next_cursor is not None

    second = await async_client.get(
        "/actors",
        headers=_auth_headers(key),
        params={"limit": 2, "cursor": first_page.next_cursor},
    )
    assert second.status_code == 200
    second_page = ListActorsResponse.model_validate(second.json())
    first_ids = {actor.id for actor in first_page.actors}
    second_ids = {actor.id for actor in second_page.actors}
    assert first_ids.isdisjoint(second_ids)
    all_active = await async_client.get(
        "/actors",
        headers=_auth_headers(key),
        params={"limit": 200},
    )
    assert all_active.status_code == 200
    all_active_page = ListActorsResponse.model_validate(all_active.json())
    all_active_ids = {actor.id for actor in all_active_page.actors}
    assert set(active_ids).issubset(all_active_ids)
    assert deactivated_id not in all_active_ids

    cursor: str | None = None
    included_ids: set[UUID] = set()
    while True:
        params: dict[str, object] = {
            "include_deactivated": "true",
            "limit": 200,
        }
        if cursor is not None:
            params["cursor"] = cursor

        with_deactivated = await async_client.get(
            "/actors",
            headers=_auth_headers(key),
            params=params,
        )
        assert with_deactivated.status_code == 200
        included = ListActorsResponse.model_validate(with_deactivated.json())
        included_ids.update(actor.id for actor in included.actors)
        cursor = included.next_cursor
        if cursor is None or deactivated_id in included_ids:
            break

    assert deactivated_id in included_ids
