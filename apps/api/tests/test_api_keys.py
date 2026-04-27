import asyncio
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
from aq_api._audit import BusinessRuleException
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.app import app
from aq_api.models import RevokeApiKeyResponse
from aq_api.services.api_keys import revoke_api_key
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "api-key-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live API key tests",
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


def _insert_actor(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
) -> UUID:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, %s)
            RETURNING id
            """,
            (actor_name, kind),
        )
        row = cursor.fetchone()
    assert row is not None
    actor_id = row[0]
    assert isinstance(actor_id, UUID)
    return actor_id


def _insert_api_key(
    conn: Connection[tuple[object, ...]],
    actor_id: UUID,
    *,
    key: str | None = None,
    revoked: bool = False,
) -> tuple[UUID, str]:
    api_key = key or f"aq2_revoke_contract_{uuid.uuid4().hex}"
    revoked_at = datetime.now(UTC) if revoked else None
    revoked_by_actor_id = actor_id if revoked else None
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO api_keys
                (actor_id, name, key_hash, prefix, lookup_id,
                 revoked_at, revoked_by_actor_id)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                actor_id,
                f"api-key-test-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
                revoked_at,
                revoked_by_actor_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    api_key_id = row[0]
    assert isinstance(api_key_id, UUID)
    return api_key_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _api_key_revocation(
    conn: Connection[tuple[object, ...]],
    api_key_id: UUID,
) -> tuple[datetime | None, UUID | None]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT revoked_at, revoked_by_actor_id
            FROM api_keys
            WHERE id = %s
            """,
            (api_key_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    revoked_at, revoked_by_actor_id = row
    assert revoked_at is None or isinstance(revoked_at, datetime)
    assert revoked_by_actor_id is None or isinstance(revoked_by_actor_id, UUID)
    return revoked_at, revoked_by_actor_id


def _active_key_count(conn: Connection[tuple[object, ...]], actor_id: UUID) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM api_keys
            WHERE actor_id = %s AND revoked_at IS NULL
            """,
            (actor_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            ORDER BY ts ASC, id ASC
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        rows = cursor.fetchall()
    return [
        {
            "op": op,
            "target_kind": target_kind,
            "target_id": str(target_id) if target_id is not None else None,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "error_code": error_code,
        }
        for op, target_kind, target_id, request_payload, response_payload, error_code
        in rows
    ]


@pytest.mark.asyncio
async def test_revoke_own_api_key_succeeds_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id = _insert_actor(conn, name="api-key-test-owner")
    _auth_key_id, auth_key = _insert_api_key(conn, actor_id)
    target_key_id, _target_key = _insert_api_key(conn, actor_id)

    response = await async_client.delete(
        f"/api-keys/{target_key_id}",
        headers=_auth_headers(auth_key),
    )

    assert response.status_code == 200
    payload = response.json()
    revoked = RevokeApiKeyResponse.model_validate(payload)
    assert revoked.api_key.id == target_key_id
    assert revoked.api_key.revoked_at is not None
    revoked_at, revoked_by_actor_id = _api_key_revocation(conn, target_key_id)
    assert revoked_at is not None
    assert revoked_by_actor_id == actor_id

    audits = _audit_rows(conn)
    assert len(audits) == 1
    assert audits[0]["op"] == "revoke_api_key"
    assert audits[0]["target_kind"] == "api_key"
    assert audits[0]["target_id"] == str(target_key_id)
    assert audits[0]["error_code"] is None


@pytest.mark.asyncio
async def test_revoke_cross_actor_returns_403_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id = _insert_actor(conn, name="api-key-test-owner")
    _auth_key_id, auth_key = _insert_api_key(conn, actor_id)
    other_actor_id = _insert_actor(conn, name="api-key-test-other")
    other_key_id, _other_key = _insert_api_key(conn, other_actor_id)

    response = await async_client.delete(
        f"/api-keys/{other_key_id}",
        headers=_auth_headers(auth_key),
    )

    assert response.status_code == 403
    assert response.content == b'{"error":"forbidden"}'
    assert _api_key_revocation(conn, other_key_id) == (None, None)

    audits = _audit_rows(conn)
    assert len(audits) == 1
    assert audits[0]["op"] == "revoke_api_key"
    assert audits[0]["target_id"] == str(other_key_id)
    assert audits[0]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_revoke_already_revoked_own_key_is_idempotent_without_duplicate_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id = _insert_actor(conn, name="api-key-test-idempotent")
    _auth_key_id, auth_key = _insert_api_key(conn, actor_id)
    target_key_id, _target_key = _insert_api_key(conn, actor_id)

    first = await async_client.delete(
        f"/api-keys/{target_key_id}",
        headers=_auth_headers(auth_key),
    )
    second = await async_client.delete(
        f"/api-keys/{target_key_id}",
        headers=_auth_headers(auth_key),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(_audit_rows(conn)) == 1


@pytest.mark.asyncio
async def test_revoke_last_active_key_returns_409_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id = _insert_actor(conn, name="api-key-test-last")
    only_key_id, only_key = _insert_api_key(conn, actor_id)

    response = await async_client.delete(
        f"/api-keys/{only_key_id}",
        headers=_auth_headers(only_key),
    )

    assert response.status_code == 409
    assert response.content == b'{"error":"cannot_revoke_last_key"}'
    assert _api_key_revocation(conn, only_key_id) == (None, None)

    audits = _audit_rows(conn)
    assert len(audits) == 1
    assert audits[0]["target_id"] == str(only_key_id)
    assert audits[0]["error_code"] == "cannot_revoke_last_key"


@pytest.mark.asyncio
async def test_revoke_concurrent_active_keys_serializes_to_one_success(
    conn: Connection[tuple[object, ...]],
) -> None:
    from aq_api._db import SessionLocal, engine

    actor_id = _insert_actor(conn, name="api-key-test-race")
    first_key_id, _first_key = _insert_api_key(conn, actor_id)
    second_key_id, _second_key = _insert_api_key(conn, actor_id)
    context_token = set_authenticated_actor_id(actor_id)

    async def revoke(api_key_id: UUID) -> object:
        async with SessionLocal() as session:
            return await revoke_api_key(
                session,
                actor_id=actor_id,
                api_key_id=api_key_id,
            )

    try:
        results = await asyncio.gather(
            revoke(first_key_id),
            revoke(second_key_id),
            return_exceptions=True,
        )
    finally:
        reset_authenticated_actor_id(context_token)
        await engine.dispose()

    successes = [
        result for result in results if isinstance(result, RevokeApiKeyResponse)
    ]
    denials = [
        result for result in results if isinstance(result, BusinessRuleException)
    ]
    assert len(successes) == 1
    assert len(denials) == 1
    assert denials[0].error_code == "cannot_revoke_last_key"
    assert _active_key_count(conn, actor_id) == 1
