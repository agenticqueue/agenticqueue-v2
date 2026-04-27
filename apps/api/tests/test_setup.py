import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlparse
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.models import SetupResponse
from aq_api.services.auth import PASSWORD_HASHER
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ALREADY_SETUP_BODY = b'{"error":"already_setup"}'
FOUNDER_ACTOR_NAME = "founder"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live setup tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        if _has_any_actor(connection) and not _is_isolated_test_db(conninfo):
            pytest.skip("setup tests require an isolated empty actor table")
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
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name = %s
            )
            """,
            (FOUNDER_ACTOR_NAME,),
        )
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name = %s)
               OR revoked_by_actor_id IN (
                    SELECT id FROM actors WHERE name = %s
               )
               OR name = %s
            """,
            (FOUNDER_ACTOR_NAME, FOUNDER_ACTOR_NAME, FOUNDER_ACTOR_NAME),
        )
        cursor.execute("DELETE FROM actors WHERE name = %s", (FOUNDER_ACTOR_NAME,))


def _is_isolated_test_db(conninfo: str) -> bool:
    database = urlparse(conninfo).path.rsplit("/", maxsplit=1)[-1]
    return database.endswith("_test")


def _has_any_actor(conn: Connection[tuple[object, ...]]) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM actors)")
        row = cursor.fetchone()
    assert row is not None
    return bool(row[0])


def _audit_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM audit_log")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _actor_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM actors")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _api_key_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM api_keys")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _founder_row(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, str, str, str, str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT actors.id, actors.name, actors.kind, api_keys.key_hash,
                   api_keys.prefix
            FROM actors
            JOIN api_keys ON api_keys.actor_id = actors.id
            WHERE actors.name = 'founder'
            """
        )
        row = cursor.fetchone()
    assert row is not None
    actor_id, actor_name, actor_kind, key_hash, prefix = row
    assert isinstance(actor_id, UUID)
    assert isinstance(actor_name, str)
    assert isinstance(actor_kind, str)
    assert isinstance(key_hash, str)
    assert isinstance(prefix, str)
    return actor_id, actor_name, actor_kind, key_hash, prefix


@pytest.mark.asyncio
async def test_setup_first_call_creates_founder_actor_and_returns_key(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    response = await async_client.post("/setup", json={})

    assert response.status_code == 200
    body = response.json()
    setup_response = SetupResponse.model_validate(body)
    founder_key = body["founder_key"]
    assert isinstance(founder_key, str)
    assert founder_key.startswith("aq2_")

    actor_id, actor_name, actor_kind, key_hash, prefix = _founder_row(conn)
    assert setup_response.actor_id == actor_id
    assert actor_name == "founder"
    assert actor_kind == "human"
    assert bool(PASSWORD_HASHER.verify(key_hash, founder_key))
    assert prefix == founder_key[:8]


@pytest.mark.asyncio
async def test_setup_second_call_returns_409_already_setup(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    first = await async_client.post("/setup", json={})
    second = await async_client.post("/setup", json={})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.content == ALREADY_SETUP_BODY
    assert _actor_count(conn) == 1
    assert _api_key_count(conn) == 1


@pytest.mark.asyncio
async def test_setup_does_not_write_audit_row(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    assert _audit_count(conn) == 0

    first = await async_client.post("/setup", json={})
    second = await async_client.post("/setup", json={})

    assert first.status_code == 200
    assert second.status_code == 409
    assert _audit_count(conn) == 0


@pytest.mark.asyncio
async def test_setup_concurrent_first_run_only_one_wins(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    first, second = await asyncio.gather(
        async_client.post("/setup", json={}),
        async_client.post("/setup", json={}),
    )
    responses = [first, second]

    assert sorted(response.status_code for response in responses) == [200, 409]
    winners = [response for response in responses if response.status_code == 200]
    losers = [response for response in responses if response.status_code == 409]
    assert len(winners) == 1
    assert len(losers) == 1
    assert "founder_key" in winners[0].json()
    assert losers[0].content == ALREADY_SETUP_BODY
    assert _actor_count(conn) == 1
    assert _api_key_count(conn) == 1


@pytest.mark.asyncio
async def test_setup_advisory_lock_uses_correct_key() -> None:
    from aq_api.services.setup import acquire_setup_lock

    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: object) -> None:
            self.statements.append(str(statement))

    session = FakeSession()

    await acquire_setup_lock(session)  # type: ignore[arg-type]

    assert session.statements == [
        "SELECT pg_advisory_xact_lock(hashtext('aq:setup-singleton'))"
    ]
