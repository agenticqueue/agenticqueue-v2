import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.models.db import Job as DbJob
from aq_api.services.auth import DISPLAY_PREFIX_LENGTH, lookup_id_for_key
from aq_api.services.claim_auto_release import run_claim_auto_release_once
from argon2 import PasswordHasher
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

SWEEP_RACE_ACTOR_PREFIX = "atomicity-submit-sweep-"
SWEEP_RACE_PROJECT_SLUG_PREFIX = "atomicity-submit-sweep-"
FAST_TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_SYNC"),
    reason="DATABASE_URL_SYNC is required for submit/sweep race tests",
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
    actor_like = f"{SWEEP_RACE_ACTOR_PREFIX}%"
    project_like = f"{SWEEP_RACE_PROJECT_SLUG_PREFIX}%"
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
    api_key = f"aq2_submit_sweep_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'agent')
            RETURNING id
            """,
            (f"{SWEEP_RACE_ACTOR_PREFIX}{uuid.uuid4().hex[:12]}",),
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
                f"{SWEEP_RACE_ACTOR_PREFIX}key-{uuid.uuid4()}",
                FAST_TEST_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )
    return actor_id, api_key


def _fixture_stale_claim(
    conn: Connection[tuple[object, ...]],
    *,
    now: datetime,
) -> tuple[UUID, str, UUID]:
    actor_id, api_key = _insert_actor_with_key(conn)
    heartbeat_at = now - timedelta(seconds=901)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Submit Sweep Race Project",
                f"{SWEEP_RACE_PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}",
                actor_id,
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
            (project_id, "submit sweep race pipeline", actor_id),
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
                "submit sweep race target",
                json.dumps({"contract_type": "test", "dod_items": []}),
                actor_id,
                heartbeat_at,
                heartbeat_at,
                actor_id,
            ),
        )
        job_row = cursor.fetchone()
        assert job_row is not None
        job_id = job_row[0]
        assert isinstance(job_id, UUID)
    return actor_id, api_key, job_id


def _done_payload() -> dict[str, object]:
    return {
        "outcome": "done",
        "dod_results": [],
        "commands_run": ["pytest -q tests/atomicity/test_submit_sweep_race.py"],
        "verification_summary": "submit/sweep race verified",
        "files_changed": ["tests/atomicity/test_submit_sweep_race.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-81",
        "decisions_made": [
            {
                "title": "Race decision",
                "statement": "Submit must not leak rows if sweep wins.",
                "rationale": "Atomicity keeps stale claims clean.",
            }
        ],
        "learnings": [
            {
                "title": "Race learning",
                "statement": "The row lock serializes submit and sweep.",
                "context": "Story 5.6",
            }
        ],
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


async def _run_sweep(now: datetime) -> int:
    from aq_api._db import SessionLocal

    async with SessionLocal() as session:
        return await run_claim_auto_release_once(session, now=now)


def _job_row(conn: Connection[tuple[object, ...]], job_id: UUID) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT state, claimed_by_actor_id, claimed_at, claim_heartbeat_at
            FROM jobs
            WHERE id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)


def _attached_counts(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM decisions
                 WHERE attached_to_kind = 'job' AND attached_to_id = %s),
                (SELECT count(*) FROM learnings
                 WHERE attached_to_kind = 'job' AND attached_to_id = %s)
            """,
            (job_id, job_id),
        )
        row = cursor.fetchone()
    assert row is not None
    return {"decisions": int(row[0]), "learnings": int(row[1])}


def _audit_counts(
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
                      AND error_code = 'job_not_in_progress'
                ),
                count(*) FILTER (
                    WHERE op = 'claim_auto_release'
                      AND error_code = 'lease_expired'
                )
            FROM audit_log
            WHERE target_kind = 'job' AND target_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "submit_success": int(row[0]),
        "submit_not_in_progress": int(row[1]),
        "sweep_release": int(row[2]),
    }


@pytest.mark.asyncio
async def test_sweep_wins_race_submit_returns_409_without_partial_state(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _actor_id, api_key, job_id = _fixture_stale_claim(conn, now=now)
    sweep_holds_lock = asyncio.Event()
    release_sweep = asyncio.Event()
    original_flush = AsyncSession.flush

    async def hold_sweep_flush(
        self: AsyncSession,
        objects: object | None = None,
    ) -> None:
        if any(
            isinstance(instance, DbJob)
            and instance.id == job_id
            and instance.state == "ready"
            for instance in self.dirty
        ):
            sweep_holds_lock.set()
            await release_sweep.wait()
        await original_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", hold_sweep_flush)

    sweep_task = asyncio.create_task(_run_sweep(now))
    await asyncio.wait_for(sweep_holds_lock.wait(), timeout=5)
    submit_task = asyncio.create_task(
        _submit_once(async_client, api_key=api_key, job_id=job_id)
    )
    await asyncio.sleep(0.1)
    release_sweep.set()

    released = await sweep_task
    status, payload = await submit_task

    assert released == 1
    assert status == 409
    assert payload["error"] == "job_not_in_progress"

    row = _job_row(conn, job_id)
    assert row["state"] == "ready"
    assert row["claimed_by_actor_id"] is None
    assert row["claimed_at"] is None
    assert row["claim_heartbeat_at"] is None
    assert _attached_counts(conn, job_id) == {"decisions": 0, "learnings": 0}
    assert _audit_counts(conn, job_id) == {
        "submit_success": 0,
        "submit_not_in_progress": 1,
        "sweep_release": 1,
    }


@pytest.mark.asyncio
async def test_submit_wins_race_sweep_noops_on_terminal_job(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _actor_id, api_key, job_id = _fixture_stale_claim(conn, now=now)

    status, payload = await _submit_once(async_client, api_key=api_key, job_id=job_id)
    released = await _run_sweep(now)

    assert status == 200
    assert payload["job"]["state"] == "done"
    assert released == 0

    row = _job_row(conn, job_id)
    assert row["state"] == "done"
    assert row["claimed_by_actor_id"] is None
    assert row["claimed_at"] is None
    assert row["claim_heartbeat_at"] is None
    assert _attached_counts(conn, job_id) == {"decisions": 1, "learnings": 1}
    assert _audit_counts(conn, job_id) == {
        "submit_success": 1,
        "submit_not_in_progress": 0,
        "sweep_release": 0,
    }
