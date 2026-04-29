import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_actor_state
from aq_api.app import app
from aq_api.models import AuditLogPage
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection
from psycopg.types.json import Jsonb

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "audit-query-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live audit query tests",
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
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_audit_query_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (actor_name,),
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
                f"audit-query-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _insert_audit(
    conn: Connection[tuple[object, ...]],
    *,
    actor_id: UUID,
    op: str,
    ts: datetime,
    target_id: UUID | None = None,
    error_code: str | None = None,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit_log
                (ts, op, authenticated_actor_id, target_kind, target_id,
                 request_payload, response_payload, error_code)
            VALUES
                (%s, %s, %s, 'actor', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                ts,
                op,
                actor_id,
                target_id,
                Jsonb({"op": op}),
                Jsonb({"ok": error_code is None}),
                error_code,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    audit_id = row[0]
    assert isinstance(audit_id, UUID)
    return audit_id


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


@pytest.mark.asyncio
async def test_audit_query_filters_and_reads_do_not_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="audit-query-test-founder")
    other_actor_id, _other_key = _insert_actor_with_key(
        conn,
        name="audit-query-test-other",
    )
    base_ts = datetime(2026, 4, 27, 10, 0, tzinfo=UTC)
    _insert_audit(conn, actor_id=actor_id, op="create_actor", ts=base_ts)
    _insert_audit(
        conn,
        actor_id=actor_id,
        op="revoke_api_key",
        ts=base_ts + timedelta(seconds=1),
        error_code="forbidden",
    )
    _insert_audit(
        conn,
        actor_id=other_actor_id,
        op="create_actor",
        ts=base_ts + timedelta(seconds=2),
    )
    before = _audit_count(conn)

    all_response = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id)},
    )
    op_response = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id), "op": "create_actor"},
    )
    actor_response = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id)},
    )

    assert all_response.status_code == 200
    all_page = AuditLogPage.model_validate(all_response.json())
    assert [entry.op for entry in all_page.entries] == [
        "revoke_api_key",
        "create_actor",
    ]
    assert op_response.status_code == 200
    assert {
        entry.op
        for entry in AuditLogPage.model_validate(op_response.json()).entries
    } == {"create_actor"}
    assert actor_response.status_code == 200
    assert {
        entry.authenticated_actor_id
        for entry in AuditLogPage.model_validate(actor_response.json()).entries
    } == {actor_id}
    assert _audit_count(conn) == before


@pytest.mark.asyncio
async def test_audit_sql_injection_probes_return_zero_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="audit-query-test-probes")
    _insert_audit(
        conn,
        actor_id=actor_id,
        op="create_actor",
        ts=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
    )
    probes = [
        {"actor": "foo' OR '1'='1"},
        {"actor": f"{actor_id}' OR '1'='1"},
        {"op": "x';--"},
        {"op": "create_actor' OR '1'='1"},
    ]

    for params in probes:
        response = await async_client.get(
            "/audit",
            headers=_auth_headers(key),
            params=params,
        )
        assert response.status_code == 200
        assert AuditLogPage.model_validate(response.json()).entries == []


@pytest.mark.asyncio
async def test_audit_pagination_cursor_round_trips_without_duplicates(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="audit-query-test-pagination")
    base_ts = datetime(2026, 4, 27, 10, 0, tzinfo=UTC)
    expected_ids = [
        _insert_audit(
            conn,
            actor_id=actor_id,
            op=f"audit_page_{index}",
            ts=base_ts + timedelta(seconds=index),
        )
        for index in range(3)
    ]

    first = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id), "limit": 2},
    )
    assert first.status_code == 200
    first_page = AuditLogPage.model_validate(first.json())
    assert len(first_page.entries) == 2
    assert first_page.next_cursor is not None

    second = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id), "limit": 2, "cursor": first_page.next_cursor},
    )
    assert second.status_code == 200
    second_page = AuditLogPage.model_validate(second.json())
    first_ids = {entry.id for entry in first_page.entries}
    second_ids = {entry.id for entry in second_page.entries}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(expected_ids)


@pytest.mark.asyncio
async def test_audit_limit_clamps_to_200(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="audit-query-test-limit")
    base_ts = datetime(2026, 4, 27, 10, 0, tzinfo=UTC)
    for index in range(205):
        _insert_audit(
            conn,
            actor_id=actor_id,
            op="limit_probe",
            ts=base_ts + timedelta(seconds=index),
        )

    response = await async_client.get(
        "/audit",
        headers=_auth_headers(key),
        params={"actor": str(actor_id), "limit": 10000},
    )

    assert response.status_code == 200
    page = AuditLogPage.model_validate(response.json())
    assert len(page.entries) == 200
    assert page.next_cursor is not None
