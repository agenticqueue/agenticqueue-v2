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
from aq_api.models import (
    ArchiveProjectResponse,
    CreateProjectResponse,
    GetProjectResponse,
    ListProjectsResponse,
    UpdateProjectResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live project tests",
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
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM audit_log")
        cursor.execute("DELETE FROM job_comments")
        cursor.execute("DELETE FROM job_edges")
        cursor.execute("DELETE FROM jobs")
        cursor.execute("DELETE FROM pipelines")
        cursor.execute("DELETE FROM labels")
        cursor.execute("DELETE FROM projects")
        cursor.execute("DELETE FROM api_keys")
        cursor.execute("DELETE FROM actors")


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"project-test-{uuid.uuid4()}"
    api_key = key or f"aq2_project_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, %s)
            RETURNING id
            """,
            (actor_name, kind),
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
                f"project-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            ORDER BY ts ASC, id ASC
            """
        )
        rows = cursor.fetchall()
    return [
        {
            "op": row[0],
            "target_kind": row[1],
            "target_id": str(row[2]) if row[2] is not None else None,
            "request_payload": row[3],
            "response_payload": row[4],
            "error_code": row[5],
        }
        for row in rows
    ]


def _project_archived_at(
    conn: Connection[tuple[object, ...]],
    project_id: UUID,
) -> datetime | None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT archived_at FROM projects WHERE id = %s",
            (project_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    archived_at = row[0]
    assert archived_at is None or isinstance(archived_at, datetime)
    return archived_at


@pytest.mark.asyncio
async def test_project_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'
    project_id = "11111111-1111-4111-8111-111111111111"

    responses = [
        await async_client.post(
            "/projects",
            json={"name": "Project Ops", "slug": "project-ops"},
        ),
        await async_client.get("/projects"),
        await async_client.get(f"/projects/{project_id}"),
        await async_client.patch(f"/projects/{project_id}", json={"name": "Renamed"}),
        await async_client.post(f"/projects/{project_id}/archive", json={}),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.content == expected


@pytest.mark.asyncio
async def test_project_rest_ops_create_list_get_update_archive_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="project-test-founder")
    headers = _auth_headers(key)

    create_response = await async_client.post(
        "/projects",
        headers=headers,
        json={
            "name": "Project Ops",
            "slug": "project-ops",
            "description": "Initial description",
        },
    )
    assert create_response.status_code == 200
    created = CreateProjectResponse.model_validate(create_response.json())
    assert created.project.name == "Project Ops"
    assert created.project.slug == "project-ops"
    assert created.project.description == "Initial description"
    assert created.project.archived_at is None
    assert created.project.created_by_actor_id == actor_id

    list_response = await async_client.get("/projects", headers=headers)
    assert list_response.status_code == 200
    listed = ListProjectsResponse.model_validate(list_response.json())
    assert [project.id for project in listed.projects] == [created.project.id]

    get_response = await async_client.get(
        f"/projects/{created.project.id}",
        headers=headers,
    )
    assert get_response.status_code == 200
    fetched = GetProjectResponse.model_validate(get_response.json())
    assert fetched.project == created.project

    update_response = await async_client.patch(
        f"/projects/{created.project.id}",
        headers=headers,
        json={"name": "Project Ops Updated", "description": "Updated"},
    )
    assert update_response.status_code == 200
    updated = UpdateProjectResponse.model_validate(update_response.json())
    assert updated.project.id == created.project.id
    assert updated.project.name == "Project Ops Updated"
    assert updated.project.slug == "project-ops"
    assert updated.project.description == "Updated"

    archive_response = await async_client.post(
        f"/projects/{created.project.id}/archive",
        headers=headers,
        json={},
    )
    assert archive_response.status_code == 200
    archived = ArchiveProjectResponse.model_validate(archive_response.json())
    assert archived.project.id == created.project.id
    assert archived.project.archived_at is not None
    assert archived.project.archived_at.tzinfo == UTC
    assert _project_archived_at(conn, created.project.id) is not None

    default_list = await async_client.get("/projects", headers=headers)
    assert default_list.status_code == 200
    default_page = ListProjectsResponse.model_validate(default_list.json())
    assert created.project.id not in {project.id for project in default_page.projects}

    archived_list = await async_client.get(
        "/projects",
        headers=headers,
        params={"include_archived": "true"},
    )
    assert archived_list.status_code == 200
    archived_page = ListProjectsResponse.model_validate(archived_list.json())
    assert created.project.id in {project.id for project in archived_page.projects}

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_project",
        "update_project",
        "archive_project",
    ]
    for row in audit_rows:
        assert row["target_kind"] == "project"
        assert row["target_id"] == str(created.project.id)
        assert row["error_code"] is None
        assert row["response_payload"] is not None


@pytest.mark.asyncio
async def test_project_slug_collision_returns_409_and_audits_slug_taken(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="project-test-founder")
    headers = _auth_headers(key)

    first = await async_client.post(
        "/projects",
        headers=headers,
        json={"name": "Project One", "slug": "project-collision"},
    )
    assert first.status_code == 200
    created = CreateProjectResponse.model_validate(first.json())

    duplicate = await async_client.post(
        "/projects",
        headers=headers,
        json={"name": "Project Two", "slug": "project-collision"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json() == {"error": "slug_taken"}
    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_project",
        "create_project",
    ]
    assert audit_rows[-1]["target_kind"] == "project"
    assert audit_rows[-1]["target_id"] == str(created.project.id)
    assert audit_rows[-1]["error_code"] == "slug_taken"

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM projects WHERE slug = %s",
            ("project-collision",),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_project_mutation_not_found_failures_are_audited(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="project-test-founder")
    headers = _auth_headers(key)
    missing_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    get_response = await async_client.get(f"/projects/{missing_id}", headers=headers)
    update_response = await async_client.patch(
        f"/projects/{missing_id}",
        headers=headers,
        json={"name": "Missing"},
    )
    archive_response = await async_client.post(
        f"/projects/{missing_id}/archive",
        headers=headers,
        json={},
    )

    assert get_response.status_code == 404
    assert get_response.json() == {"error": "project_not_found"}
    assert update_response.status_code == 404
    assert update_response.json() == {"error": "project_not_found"}
    assert archive_response.status_code == 404
    assert archive_response.json() == {"error": "project_not_found"}
    assert _audit_rows(conn) == [
        {
            "op": "update_project",
            "target_kind": "project",
            "target_id": str(missing_id),
            "request_payload": {
                "project_id": str(missing_id),
                "name": "Missing",
                "description": None,
            },
            "response_payload": {"error": "project_not_found"},
            "error_code": "project_not_found",
        },
        {
            "op": "archive_project",
            "target_kind": "project",
            "target_id": str(missing_id),
            "request_payload": {"project_id": str(missing_id)},
            "response_payload": {"error": "project_not_found"},
            "error_code": "project_not_found",
        },
    ]
