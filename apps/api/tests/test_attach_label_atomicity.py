import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    audit_rows,
    auth_headers,
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    job_row,
    truncate_job_state,
)
from aq_api.app import app
from aq_api.models.db import Job as DbJob
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live attach_label atomicity tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


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


def _register_label(
    conn: Connection[tuple[object, ...]],
    *,
    project_id: UUID,
    name: str,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO labels (project_id, name)
            VALUES (%s, %s)
            """,
            (project_id, name),
        )


def _fixture_job(conn: Connection[tuple[object, ...]]) -> tuple[str, UUID]:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="attach atomicity target",
    )
    _register_label(conn, project_id=project_id, name="area:web")
    return key, job_id


@pytest.mark.asyncio
async def test_attach_label_rolls_back_text_array_and_audit_on_flush_failure(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, job_id = _fixture_job(conn)
    original_flush = AsyncSession.flush

    async def fail_job_label_flush(
        self: AsyncSession,
        objects: object | None = None,
    ) -> None:
        if any(
            isinstance(instance, DbJob) and "area:web" in (instance.labels or [])
            for instance in self.dirty
        ):
            raise RuntimeError("forced attach_label flush failure")
        await original_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", fail_job_label_flush)

    response = await async_client.post(
        f"/jobs/{job_id}/labels",
        headers=auth_headers(key),
        json={"label_name": "area:web"},
    )

    assert response.status_code == 500
    assert job_row(conn, job_id)["labels"] == []
    assert audit_rows(conn) == []
