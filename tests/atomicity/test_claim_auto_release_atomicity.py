import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from aq_api._db import SessionLocal, engine
from aq_api.models.db import Job as DbJob
from aq_api.services.claim_auto_release import run_claim_auto_release_once
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "atomicity-sweep-"
PROJECT_SLUG_PREFIX = "atomicity-sweep-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live sweep atomicity tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_state(connection)
        yield connection
        _truncate_state(connection)


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_after_test() -> AsyncIterator[None]:
    yield
    await engine.dispose()


def _truncate_state(conn: Connection[tuple[object, ...]]) -> None:
    actor_like = f"{ACTOR_PREFIX}%"
    project_like = f"{PROJECT_SLUG_PREFIX}%"
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
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))


def _insert_actor(conn: Connection[tuple[object, ...]]) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'agent')
            RETURNING id
            """,
            (f"{ACTOR_PREFIX}{uuid.uuid4().hex[:12]}",),
        )
        row = cursor.fetchone()
    assert row is not None
    actor_id = row[0]
    assert isinstance(actor_id, UUID)
    return actor_id


def _fixture_stale_jobs(
    conn: Connection[tuple[object, ...]],
    *,
    now: datetime,
) -> list[UUID]:
    actor_id = _insert_actor(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Sweep Atomicity Project",
                f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}",
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
            (project_id, "sweep atomicity pipeline", actor_id),
        )
        pipeline_row = cursor.fetchone()
        assert pipeline_row is not None
        pipeline_id = pipeline_row[0]
        assert isinstance(pipeline_id, UUID)

        job_ids: list[UUID] = []
        for index in range(5):
            heartbeat_at = now - timedelta(seconds=901 + index)
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
                    f"sweep atomicity target {index}",
                    json.dumps({"contract_type": "test", "dod_items": []}),
                    actor_id,
                    heartbeat_at,
                    heartbeat_at,
                    actor_id,
                ),
            )
            row = cursor.fetchone()
            assert row is not None
            job_id = row[0]
            assert isinstance(job_id, UUID)
            job_ids.append(job_id)
    return job_ids


def _job_state(conn: Connection[tuple[object, ...]], job_id: UUID) -> str:
    with conn.cursor() as cursor:
        cursor.execute("SELECT state FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _auto_release_audit_count(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM audit_log
            WHERE op = 'claim_auto_release'
              AND target_kind = 'job'
              AND target_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_claim_auto_release_commits_per_job_when_mid_batch_flush_fails(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    job_ids = _fixture_stale_jobs(conn, now=now)
    release_order = list(reversed(job_ids))
    failing_job_id = job_ids[2]
    original_flush = AsyncSession.flush

    async def fail_third_release_flush(
        self: AsyncSession,
        objects: object | None = None,
    ) -> None:
        if any(
            isinstance(instance, DbJob)
            and instance.id == failing_job_id
            and instance.state == "ready"
            for instance in self.dirty
        ):
            raise RuntimeError("forced sweep release flush failure")
        await original_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", fail_third_release_flush)

    with pytest.raises(RuntimeError, match="forced sweep release flush failure"):
        async with SessionLocal() as session:
            await run_claim_auto_release_once(session, now=now)

    for released_job_id in release_order[:2]:
        assert _job_state(conn, released_job_id) == "ready"
        assert _auto_release_audit_count(conn, released_job_id) == 1

    assert _job_state(conn, failing_job_id) == "in_progress"
    assert _auto_release_audit_count(conn, failing_job_id) == 0

    for untouched_job_id in release_order[3:]:
        assert _job_state(conn, untouched_job_id) == "in_progress"
        assert _auto_release_audit_count(conn, untouched_job_id) == 0
