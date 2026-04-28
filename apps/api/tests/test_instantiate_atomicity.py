import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_cap03_state
from aq_api.app import app
from aq_api.models.db import Job as DbJob
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "instantiate-atomicity-test-"
PROJECT_SLUG_PREFIX = "instantiate-atomicity-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live instantiate atomicity tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_cap03_state(connection)
        yield connection
        _truncate_cap03_state(connection)


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


def _truncate_cap03_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_cap03_state(
        conn,
        actor_name_prefix=ACTOR_PREFIX,
        project_slug_prefix=PROJECT_SLUG_PREFIX,
    )


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = f"aq2_instantiate_atomicity_{uuid.uuid4().hex}"
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
                f"instantiate-atomicity-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _insert_project(
    conn: Connection[tuple[object, ...]],
    *,
    created_by_actor_id: UUID,
) -> UUID:
    slug = f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            ("Instantiate Atomicity Project", slug, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def _contract_profile_id(conn: Connection[tuple[object, ...]], name: str) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM contract_profiles WHERE name = %s LIMIT 1",
            (name,),
        )
        row = cursor.fetchone()
    assert row is not None
    profile_id = row[0]
    assert isinstance(profile_id, UUID)
    return profile_id


def _insert_workflow(
    conn: Connection[tuple[object, ...]],
    *,
    slug: str,
    created_by_actor_id: UUID,
    contract_profile_id: UUID,
) -> tuple[UUID, UUID]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workflows (slug, name, version, created_by_actor_id)
            VALUES (%s, %s, 1, %s)
            RETURNING id
            """,
            (slug, "Instantiate Atomicity Workflow", created_by_actor_id),
        )
        workflow_row = cursor.fetchone()
        assert workflow_row is not None
        workflow_id = workflow_row[0]
        cursor.execute(
            """
            INSERT INTO workflow_steps
                (workflow_id, name, ordinal, default_contract_profile_id, step_edges)
            VALUES (%s, %s, %s, %s, '{}'::jsonb)
            RETURNING id
            """,
            (workflow_id, "build", 1, contract_profile_id),
        )
        step_row = cursor.fetchone()
    assert isinstance(workflow_id, UUID)
    assert step_row is not None
    step_id = step_row[0]
    assert isinstance(step_id, UUID)
    return workflow_id, step_id


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _pipeline_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM pipelines
            WHERE created_by_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
               OR project_id IN (
                    SELECT id
                    FROM projects
                    WHERE slug LIKE %s
                       OR created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (f"{ACTOR_PREFIX}%", f"{PROJECT_SLUG_PREFIX}%", f"{ACTOR_PREFIX}%"),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _job_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM jobs
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id
                    FROM projects
                    WHERE slug LIKE %s
                       OR created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (f"{ACTOR_PREFIX}%", f"{PROJECT_SLUG_PREFIX}%", f"{ACTOR_PREFIX}%"),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _job_edge_count(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM job_edges
            WHERE from_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
                       OR projects.created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
               OR to_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
                       OR projects.created_by_actor_id IN (
                            SELECT id FROM actors WHERE name LIKE %s
                       )
               )
            """,
            (
                f"{PROJECT_SLUG_PREFIX}%",
                f"{ACTOR_PREFIX}%",
                f"{PROJECT_SLUG_PREFIX}%",
                f"{ACTOR_PREFIX}%",
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


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
async def test_instantiate_pipeline_failure_rolls_back_pipeline_jobs_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(
        conn,
        name="instantiate-atomicity-test-founder",
    )
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    contract_profile_id = _contract_profile_id(conn, "coding-task")
    workflow_slug = "instantiate-atomicity-test-flow"
    workflow_id, step_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        created_by_actor_id=actor_id,
        contract_profile_id=contract_profile_id,
    )

    async def fail_after_first_job(
        session: object,
        *,
        pipeline_id: UUID,
        project_id: UUID,
        actor_id: UUID,
        workflow_steps: list[object],
    ) -> list[DbJob]:
        first_step = workflow_steps[0]
        session.add(
            DbJob(
                pipeline_id=pipeline_id,
                project_id=project_id,
                state="ready",
                title=first_step.name,
                contract_profile_id=first_step.default_contract_profile_id,
                instantiated_from_step_id=first_step.id,
                created_by_actor_id=actor_id,
            )
        )
        await session.flush()
        raise RuntimeError("step insert failed")

    monkeypatch.setattr(
        "aq_api.services.instantiate._create_jobs_from_steps",
        fail_after_first_job,
    )

    with pytest.raises(RuntimeError, match="step insert failed"):
        await async_client.post(
            f"/pipelines/from-workflow/{workflow_slug}",
            headers=_auth_headers(key),
            json={
                "project_id": str(project_id),
                "pipeline_name": "rollback-target",
            },
        )

    assert workflow_id is not None
    assert step_id is not None
    assert _pipeline_count(conn) == 0
    assert _job_count(conn) == 0
    assert _job_edge_count(conn) == 0
    assert _audit_count(conn) == 0
