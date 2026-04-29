import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    lookup_id_for_key,
)
from argon2 import PasswordHasher
from psycopg import Connection

RACE_ACTOR_PREFIX = "parity-test-race-"
RACE_PROJECT_SLUG_PREFIX = "parity-race-"
CLAIMER_COUNT = 50
FAST_TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture()
def conn(db_url_sync: str | None) -> Iterator[Connection[tuple[object, ...]]]:
    if db_url_sync is None:
        pytest.skip("DATABASE_URL_SYNC is required for claim race tests")
    conninfo = db_url_sync.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_state(connection)
        yield connection
        _truncate_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    from aq_api._db import engine

    await engine.dispose()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=20,
    ) as client:
        yield client

    await engine.dispose()


def _truncate_state(conn: Connection[tuple[object, ...]]) -> None:
    actor_like = f"{RACE_ACTOR_PREFIX}%"
    project_like = f"{RACE_PROJECT_SLUG_PREFIX}%"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
               OR (
                    target_kind = 'job'
                    AND target_id IN (
                        SELECT jobs.id
                        FROM jobs
                        JOIN projects ON projects.id = jobs.project_id
                        WHERE projects.slug LIKE %s
                    )
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM jobs
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM pipelines
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute("DELETE FROM projects WHERE slug LIKE %s", (project_like,))
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR revoked_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (actor_like, actor_like),
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))


def _insert_actor_with_key(conn: Connection[tuple[object, ...]]) -> tuple[UUID, str]:
    api_key = f"aq2_claim_race_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'agent')
            RETURNING id
            """,
            (f"{RACE_ACTOR_PREFIX}{uuid.uuid4().hex[:12]}",),
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
                f"{RACE_ACTOR_PREFIX}key-{uuid.uuid4()}",
                FAST_TEST_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )
    return actor_id, api_key


def _fixture_queue(
    conn: Connection[tuple[object, ...]],
    *,
    pool_size: int,
) -> tuple[UUID, list[str]]:
    actors = [_insert_actor_with_key(conn) for _ in range(CLAIMER_COUNT)]
    owner_actor_id = actors[0][0]
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                f"Claim Race {pool_size}",
                f"{RACE_PROJECT_SLUG_PREFIX}{pool_size}-{uuid.uuid4().hex[:12]}",
                owner_actor_id,
            ),
        )
        project_row = cursor.fetchone()
        assert project_row is not None
        project_id = project_row[0]
        assert isinstance(project_id, UUID)

        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_id, f"race pipeline {pool_size}", owner_actor_id),
        )
        pipeline_row = cursor.fetchone()
        assert pipeline_row is not None
        pipeline_id = pipeline_row[0]
        assert isinstance(pipeline_id, UUID)

        for index in range(pool_size):
            cursor.execute(
                """
                INSERT INTO jobs
                    (
                        pipeline_id,
                        project_id,
                        state,
                        title,
                        contract,
                        created_by_actor_id
                    )
                VALUES (%s, %s, 'ready', %s, %s::jsonb, %s)
                """,
                (
                    pipeline_id,
                    project_id,
                    f"race job {pool_size}-{index}",
                    json.dumps({"contract_type": "test", "dod_items": []}),
                    owner_actor_id,
                ),
            )
    return project_id, [api_key for _actor_id, api_key in actors]


async def _claim_once(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    project_id: UUID,
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        "/jobs/claim",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"project_id": str(project_id)},
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return response.status_code, payload


def _audit_counts(conn: Connection[tuple[object, ...]]) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE op = 'claim_next_job' AND error_code IS NULL
                ),
                count(*) FILTER (
                    WHERE op = 'claim_next_job' AND error_code = 'no_ready_job'
                ),
                count(*) FILTER (WHERE op = 'claim_next_job')
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            """,
            (f"{RACE_ACTOR_PREFIX}%",),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "success": int(row[0]),
        "no_ready_job": int(row[1]),
        "total": int(row[2]),
    }


def _claimed_job_count(conn: Connection[tuple[object, ...]], project_id: UUID) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM jobs
            WHERE project_id = %s
              AND state = 'in_progress'
              AND claimed_by_actor_id IS NOT NULL
            """,
            (project_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("pool_size", [1, 5])
@pytest.mark.asyncio
async def test_fifty_concurrent_claimers_get_unique_winners_and_audit_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    pool_size: int,
) -> None:
    project_id, api_keys = _fixture_queue(conn, pool_size=pool_size)

    gathered = await asyncio.gather(
        *[
            _claim_once(async_client, api_key=api_key, project_id=project_id)
            for api_key in api_keys
        ],
        return_exceptions=True,
    )

    exceptions = [result for result in gathered if isinstance(result, Exception)]
    assert exceptions == []
    results = [
        result for result in gathered if not isinstance(result, Exception)
    ]
    successes = [payload for status, payload in results if status == 200]
    denials = [payload for status, payload in results if status == 409]
    unexpected = [
        (status, payload)
        for status, payload in results
        if status not in {200, 409}
    ]
    assert unexpected == []
    assert len(successes) == pool_size
    assert len(denials) == CLAIMER_COUNT - pool_size
    assert {payload["error"] for payload in denials} == {"no_ready_job"}

    claimed_job_ids = [payload["job"]["id"] for payload in successes]
    assert len(set(claimed_job_ids)) == pool_size
    assert _claimed_job_count(conn, project_id) == pool_size
    assert _audit_counts(conn) == {
        "success": pool_size,
        "no_ready_job": CLAIMER_COUNT - pool_size,
        "total": CLAIMER_COUNT,
    }
