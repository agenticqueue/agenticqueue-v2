import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from aq_api.services.auth import PASSWORD_HASHER, lookup_id_for_key, resolve_actor
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live auth lookup tests",
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
async def session() -> AsyncIterator[AsyncSession]:
    from aq_api._db import SessionLocal, engine

    async with SessionLocal() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


def _truncate_cap02_state(conn: Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM audit_log")
        cursor.execute("DELETE FROM api_keys")
        cursor.execute("DELETE FROM actors")


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    key: str,
    *,
    actor_name: str | None = None,
) -> UUID:
    actor_name = actor_name or f"lookup-test-{uuid.uuid4()}"
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
                f"lookup-test-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(key),
                key[:8],
                lookup_id_for_key(key),
            ),
        )

    return actor_id


@pytest.mark.asyncio
async def test_resolve_actor_verifies_once_with_prefix_collisions(
    monkeypatch: pytest.MonkeyPatch,
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    valid_key = "aq2same_valid_lookup_contract_key"
    for index in range(100):
        _insert_actor_with_key(conn, f"aq2same_collision_{index:03d}")
    _insert_actor_with_key(conn, valid_key)
    calls: list[tuple[str, str]] = []

    class FakePasswordHasher:
        def verify(self, key_hash: str, key: str) -> bool:
            calls.append((key_hash, key))
            return key == valid_key

    monkeypatch.setattr("aq_api.services.auth.PASSWORD_HASHER", FakePasswordHasher())

    actor = await resolve_actor(session, valid_key)

    assert actor is not None
    assert [key for _key_hash, key in calls] == [valid_key]


@pytest.mark.asyncio
async def test_failed_auth_time_is_independent_of_prefix_collision_count(
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    for index in range(100):
        _insert_actor_with_key(conn, f"aq2same_collision_{index:03d}")

    baseline_hash = PASSWORD_HASHER.hash("lookup-baseline")
    baseline_start = time.perf_counter()
    assert bool(PASSWORD_HASHER.verify(baseline_hash, "lookup-baseline"))
    single_verify_seconds = time.perf_counter() - baseline_start

    failed_start = time.perf_counter()
    actor = await resolve_actor(session, "aq2same_missing_lookup_key")
    failed_seconds = time.perf_counter() - failed_start

    assert actor is None
    assert failed_seconds < single_verify_seconds * 2


def test_lookup_secret_field_is_redacted() -> None:
    from aq_api.services.audit import redact_secrets

    assert redact_secrets({"AQ_KEY_LOOKUP_SECRET": "literal-secret-value"}) == {
        "AQ_KEY_LOOKUP_SECRET": "[REDACTED]"
    }
