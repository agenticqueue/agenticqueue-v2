import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.services.auth import DISPLAY_PREFIX_LENGTH, lookup_id_for_key
from argon2 import PasswordHasher
from psycopg import Connection

SUBMIT_RACE_ACTOR_PREFIX = "atomicity-submit-race-"
SUBMIT_RACE_PROJECT_SLUG_PREFIX = "atomicity-submit-race-"
SUBMITTER_COUNT = 50
FAST_TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_SYNC"),
    reason="DATABASE_URL_SYNC is required for submit race tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    database_url = os.environ["DATABASE_URL_SYNC"]
    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
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
        timeout=30,
    ) as client:
        yield client

    await engine.dispose()


def _truncate_state(conn: Connection[tuple[object, ...]]) -> None:
    actor_like = f"{SUBMIT_RACE_ACTOR_PREFIX}%"
    project_like = f"{SUBMIT_RACE_PROJECT_SLUG_PREFIX}%"
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
            DELETE FROM decisions
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR (
                    attached_to_kind = 'job'
                    AND attached_to_id IN (
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
            DELETE FROM learnings
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR (
                    attached_to_kind = 'job'
                    AND attached_to_id IN (
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
            DELETE FROM job_edges
            WHERE from_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
               )
               OR to_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
               )
            """,
            (project_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM jobs
            WHERE project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
               OR created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR claimed_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (project_like, actor_like, actor_like),
        )
        cursor.execute(
            """
            DELETE FROM pipelines
            WHERE project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
               OR created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (project_like, actor_like),
        )
        cursor.execute("DELETE FROM projects WHERE slug LIKE %s", (project_like,))
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
            """,
            (actor_like,),
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))


def _insert_actor_with_key(conn: Connection[tuple[object, ...]]) -> tuple[UUID, str]:
    api_key = f"aq2_submit_race_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'agent')
            RETURNING id
            """,
            (f"{SUBMIT_RACE_ACTOR_PREFIX}{uuid.uuid4().hex[:12]}",),
        )
        row = cursor.fetchone()
        assert row is not None
        actor_id = row[0]
        assert isinstance(actor_id, UUID)
        cursor.execute(
            """
            INSERT INTO api_keys (actor_id, name, key_hash, prefix, lookup_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                actor_id,
                f"{SUBMIT_RACE_ACTOR_PREFIX}key-{uuid.uuid4()}",
                FAST_TEST_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )
    return actor_id, api_key


def _fixture_claimed_job(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, list[str]]:
    actors = [_insert_actor_with_key(conn) for _ in range(SUBMITTER_COUNT)]
    claimant_actor_id = actors[0][0]
    claimed_at = datetime.now(UTC)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Submit Race Project",
                f"{SUBMIT_RACE_PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}",
                claimant_actor_id,
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
            (project_id, "submit race pipeline", claimant_actor_id),
        )
        pipeline_row = cursor.fetchone()
        assert pipeline_row is not None
        pipeline_id = pipeline_row[0]
        assert isinstance(pipeline_id, UUID)

        cursor.execute(
            """
            INSERT INTO jobs
                (
                    pipeline_id,
                    project_id,
                    state,
                    title,
                    contract,
                    claimed_by_actor_id,
                    claimed_at,
                    claim_heartbeat_at,
                    created_by_actor_id
                )
            VALUES
                (%s, %s, 'in_progress', %s, %s::jsonb, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                pipeline_id,
                project_id,
                "submit race target",
                json.dumps({"contract_type": "test", "dod_items": []}),
                claimant_actor_id,
                claimed_at,
                claimed_at,
                claimant_actor_id,
            ),
        )
        job_row = cursor.fetchone()
        assert job_row is not None
        job_id = job_row[0]
        assert isinstance(job_id, UUID)
    return job_id, [api_key for _actor_id, api_key in actors]


def _done_payload() -> dict[str, object]:
    return {
        "outcome": "done",
        "dod_results": [],
        "commands_run": ["pytest -q tests/atomicity/test_submit_concurrent_race.py"],
        "verification_summary": "concurrent submit race verified",
        "files_changed": ["tests/atomicity/test_submit_concurrent_race.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-81",
        "decisions_made": [],
        "learnings": [],
    }


async def _submit_once(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    job_id: UUID,
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        f"/jobs/{job_id}/submit",
        headers={"Authorization": f"Bearer {api_key}"},
        json=_done_payload(),
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return response.status_code, payload


def _submit_audit_counts(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE op = 'submit_job' AND error_code IS NULL
                ),
                count(*) FILTER (
                    WHERE op = 'submit_job'
                      AND error_code = 'submit_forbidden'
                ),
                count(*) FILTER (
                    WHERE op = 'submit_job'
                      AND error_code = 'job_not_in_progress'
                ),
                count(*) FILTER (WHERE op = 'submit_job')
            FROM audit_log
            WHERE target_kind = 'job' AND target_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "success": int(row[0]),
        "submit_forbidden": int(row[1]),
        "job_not_in_progress": int(row[2]),
        "total": int(row[3]),
    }


def _done_job_count(conn: Connection[tuple[object, ...]], job_id: UUID) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM jobs
            WHERE id = %s AND state = 'done'
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_fifty_concurrent_submit_attempts_get_one_winner_and_audit_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    job_id, api_keys = _fixture_claimed_job(conn)

    gathered = await asyncio.gather(
        *[
            _submit_once(async_client, api_key=api_key, job_id=job_id)
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
    denials = [
        (status, payload)
        for status, payload in results
        if status in {403, 409}
    ]
    unexpected = [
        (status, payload)
        for status, payload in results
        if status not in {200, 403, 409}
    ]
    assert unexpected == []
    assert len(successes) == 1
    assert successes[0]["job"]["id"] == str(job_id)
    assert successes[0]["job"]["state"] == "done"
    assert len(denials) == SUBMITTER_COUNT - 1
    assert {payload["error"] for _status, payload in denials} <= {
        "submit_forbidden",
        "job_not_in_progress",
    }

    audit_counts = _submit_audit_counts(conn, job_id)
    assert audit_counts["success"] == 1
    assert audit_counts["submit_forbidden"] + audit_counts["job_not_in_progress"] == (
        SUBMITTER_COUNT - 1
    )
    assert audit_counts["total"] == SUBMITTER_COUNT
    assert _done_job_count(conn, job_id) == 1
