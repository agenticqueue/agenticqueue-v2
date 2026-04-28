import json
import os
from collections.abc import AsyncIterator, Iterator

import httpx
import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    audit_rows,
    auth_headers,
    insert_actor_with_key,
    insert_comment,
    insert_job,
    insert_pipeline,
    insert_project,
    truncate_job_state,
)
from aq_api.app import app
from aq_api.models import CommentOnJobResponse, ListJobCommentsResponse
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live job comment tests",
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    from aq_api._db import engine

    await engine.dispose()


def _fixture_job(conn: Connection[tuple[object, ...]]) -> tuple[str, str, str]:
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
        title="comment target",
    )
    return str(actor_id), key, str(job_id)


def _comment_bodies(conn: Connection[tuple[object, ...]]) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT body FROM job_comments ORDER BY created_at ASC, id ASC")
        return [str(row[0]) for row in cursor.fetchall()]


@pytest.mark.asyncio
async def test_comment_on_job_audits_body_length_only(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, job_id = _fixture_job(conn)
    body = "This note is stored in job_comments, not audit_log."

    response = await async_client.post(
        f"/jobs/{job_id}/comments",
        headers=auth_headers(key),
        json={"body": body},
    )

    assert response.status_code == 200
    payload = CommentOnJobResponse.model_validate(response.json())
    assert str(payload.comment.job_id) == job_id
    assert str(payload.comment.author_actor_id) == actor_id
    assert payload.comment.body == body
    assert _comment_bodies(conn) == [body]

    rows = audit_rows(conn)
    assert rows == [
        {
            "op": "comment_on_job",
            "target_kind": "job",
            "target_id": job_id,
            "request_payload": {"job_id": job_id, "body_length": len(body)},
            "response_payload": {
                "comment_id": str(payload.comment.id),
                "body_length": len(body),
            },
            "error_code": None,
        }
    ]
    assert body not in json.dumps(rows, sort_keys=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    ["", "contains\u0000null", "x" * 16385],
)
async def test_comment_body_bounds_are_enforced(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    body: str,
) -> None:
    _actor_id, key, job_id = _fixture_job(conn)

    response = await async_client.post(
        f"/jobs/{job_id}/comments",
        headers=auth_headers(key),
        json={"body": body},
    )

    assert response.status_code == 422
    assert _comment_bodies(conn) == []
    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_list_job_comments_paginates_fifo_and_never_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id_raw, key, job_id_raw = _fixture_job(conn)
    import uuid

    actor_id = uuid.UUID(actor_id_raw)
    job_id = uuid.UUID(job_id_raw)
    first = insert_comment(
        conn,
        job_id=job_id,
        author_actor_id=actor_id,
        body="first",
        created_at_offset_seconds=-30,
    )
    second = insert_comment(
        conn,
        job_id=job_id,
        author_actor_id=actor_id,
        body="second",
        created_at_offset_seconds=-20,
    )
    third = insert_comment(
        conn,
        job_id=job_id,
        author_actor_id=actor_id,
        body="third",
        created_at_offset_seconds=-10,
    )
    assert audit_rows(conn) == []

    first_page_response = await async_client.get(
        f"/jobs/{job_id}/comments",
        headers=auth_headers(key),
        params={"limit": 2},
    )
    assert first_page_response.status_code == 200
    first_page = ListJobCommentsResponse.model_validate(first_page_response.json())
    assert [comment.id for comment in first_page.comments] == [first, second]
    assert first_page.next_cursor is not None

    fourth = insert_comment(
        conn,
        job_id=job_id,
        author_actor_id=actor_id,
        body="fourth",
        created_at_offset_seconds=10,
    )
    for _ in range(100):
        list_response = await async_client.get(
            f"/jobs/{job_id}/comments",
            headers=auth_headers(key),
            params={"limit": 2, "cursor": first_page.next_cursor},
        )
        assert list_response.status_code == 200

    second_page = ListJobCommentsResponse.model_validate(list_response.json())
    assert [comment.id for comment in second_page.comments] == [third, fourth]
    assert {comment.id for comment in first_page.comments}.isdisjoint(
        {comment.id for comment in second_page.comments}
    )
    assert audit_rows(conn) == []
