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
from aq_api.services import pipelines as pipeline_services
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "clone-atomicity-test-"
PROJECT_SLUG_PREFIX = "clone-atomicity-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live clone atomicity tests",
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
            """,
            (actor_like,),
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
    api_key = f"aq2_clone_atomicity_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (f"{ACTOR_PREFIX}{uuid.uuid4()}",),
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
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )
    return actor_id, api_key


def _insert_project(conn: Connection[tuple[object, ...]], actor_id: UUID) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Clone Atomicity Test",
                f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}",
                actor_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def _insert_source_pipeline(
    conn: Connection[tuple[object, ...]],
    *,
    actor_id: UUID,
    project_id: UUID,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, is_template, created_by_actor_id)
            VALUES (%s, 'atomic-source', true, %s)
            RETURNING id
            """,
            (project_id, actor_id),
        )
        row = cursor.fetchone()
        assert row is not None
        pipeline_id = row[0]
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
            VALUES
                (%s, %s, 'ready', 'scope', %s::jsonb, %s)
            """,
            (
                pipeline_id,
                project_id,
                json.dumps(
                    {
                        "contract_type": "scoping",
                        "dod_items": [{"id": "scope-statement"}],
                    }
                ),
                actor_id,
            ),
        )
    return pipeline_id


def _counts(conn: Connection[tuple[object, ...]], project_id: UUID) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (
                    SELECT count(*)
                    FROM pipelines
                    WHERE project_id = %s
                      AND name = 'atomic-clone'
                ),
                (
                    SELECT count(*)
                    FROM jobs
                    WHERE project_id = %s
                      AND title = 'scope'
                      AND pipeline_id IN (
                        SELECT id
                        FROM pipelines
                        WHERE project_id = %s
                          AND name = 'atomic-clone'
                      )
                ),
                (
                    SELECT count(*)
                    FROM audit_log
                    WHERE authenticated_actor_id IN (
                        SELECT id FROM actors WHERE name LIKE %s
                    )
                      AND op = 'clone_pipeline'
                )
            """,
            (project_id, project_id, project_id, f"{ACTOR_PREFIX}%"),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "pipelines": int(row[0]),
        "jobs": int(row[1]),
        "audit_rows": int(row[2]),
    }


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.asyncio
async def test_clone_pipeline_rolls_back_pipeline_jobs_and_audit_on_mid_clone_failure(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, api_key = _insert_actor_with_key(conn)
    project_id = _insert_project(conn, actor_id)
    source_id = _insert_source_pipeline(conn, actor_id=actor_id, project_id=project_id)

    async def fail_after_clone_pipeline_created(
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise RuntimeError("forced clone failure")

    monkeypatch.setattr(
        pipeline_services,
        "_clone_source_jobs",
        fail_after_clone_pipeline_created,
        raising=False,
    )

    response = await async_client.post(
        f"/pipelines/{source_id}/clone",
        headers=_auth_headers(api_key),
        json={"name": "atomic-clone"},
    )

    assert response.status_code == 500
    assert _counts(conn, project_id) == {
        "pipelines": 0,
        "jobs": 0,
        "audit_rows": 0,
    }
