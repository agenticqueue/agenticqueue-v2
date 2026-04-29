import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.models.db import Job as DbJob
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "atomicity-claim-"
PROJECT_SLUG_PREFIX = "atomicity-claim-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live claim atomicity tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_state(connection)
        yield connection
        _truncate_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    from aq_api._db import engine

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


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, str]:
    actor_key = f"aq2_atomicity_claim_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (f"{ACTOR_PREFIX}{uuid.uuid4().hex[:12]}",),
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
                f"{ACTOR_PREFIX}key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(actor_key),
                actor_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(actor_key),
            ),
        )
    return actor_id, actor_key


def _fixture_job(conn: Connection[tuple[object, ...]]) -> tuple[str, UUID, UUID]:
    actor_id, key = _insert_actor_with_key(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Claim Atomicity Project",
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
            (project_id, "claim atomicity pipeline", actor_id),
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
                    created_by_actor_id
                )
            VALUES (%s, %s, 'ready', %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                pipeline_id,
                project_id,
                "claim atomicity target",
                json.dumps({"contract_type": "test", "dod_items": []}),
                actor_id,
            ),
        )
        job_row = cursor.fetchone()
        assert job_row is not None
        job_id = job_row[0]
        assert isinstance(job_id, UUID)

    return key, project_id, job_id


def _job_state(conn: Connection[tuple[object, ...]], job_id: UUID) -> dict[str, object]:
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


def _claim_audit_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM audit_log
            WHERE op = 'claim_next_job'
              AND authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
              )
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.asyncio
async def test_claim_next_job_rolls_back_state_and_audit_on_flush_failure(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, project_id, job_id = _fixture_job(conn)
    original_flush = AsyncSession.flush

    async def fail_claim_flush(
        self: AsyncSession,
        objects: object | None = None,
    ) -> None:
        if any(
            isinstance(instance, DbJob) and instance.state == "in_progress"
            for instance in self.dirty
        ):
            raise RuntimeError("forced claim flush failure")
        await original_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", fail_claim_flush)

    response = await async_client.post(
        "/jobs/claim",
        headers=_auth_headers(key),
        json={"project_id": str(project_id)},
    )

    assert response.status_code == 500
    stored = _job_state(conn, job_id)
    assert stored["state"] == "ready"
    assert stored["claimed_by_actor_id"] is None
    assert stored["claimed_at"] is None
    assert stored["claim_heartbeat_at"] is None
    assert _claim_audit_count(conn) == 0
