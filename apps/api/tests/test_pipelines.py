import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_cap03_state
from aq_api.app import app
from aq_api.models import (
    CreatePipelineResponse,
    GetPipelineResponse,
    ListPipelinesResponse,
    UpdatePipelineResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "pipeline-test-"
PROJECT_SLUG_PREFIX = "pipeline-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live pipeline tests",
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
    kind: str = "human",
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_pipeline_contract_{uuid.uuid4().hex}"
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
                f"pipeline-test-key-{uuid.uuid4()}",
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
    slug: str | None = None,
    name: str = "Pipeline Test Project",
) -> UUID:
    project_slug = slug or f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (name, project_slug, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def _insert_pipeline(
    conn: Connection[tuple[object, ...]],
    *,
    project_id: UUID,
    name: str,
    created_by_actor_id: UUID,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_id, name, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    pipeline_id = row[0]
    assert isinstance(pipeline_id, UUID)
    return pipeline_id


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _pipeline_workflow_links(
    conn: Connection[tuple[object, ...]],
    pipeline_id: UUID,
) -> tuple[UUID | None, int | None]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT instantiated_from_workflow_id, instantiated_from_workflow_version
            FROM pipelines
            WHERE id = %s
            """,
            (pipeline_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1]


def _audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            ORDER BY ts ASC, id ASC
            """,
            (f"{ACTOR_PREFIX}%",),
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


@pytest.mark.asyncio
async def test_pipeline_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'
    pipeline_id = "11111111-1111-4111-8111-111111111111"
    project_id = "22222222-2222-4222-8222-222222222222"

    responses = [
        await async_client.post(
            "/pipelines",
            json={"project_id": project_id, "name": "No Auth"},
        ),
        await async_client.get("/pipelines"),
        await async_client.get(f"/pipelines/{pipeline_id}"),
        await async_client.patch(
            f"/pipelines/{pipeline_id}",
            json={"name": "No Auth Update"},
        ),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.content == expected


@pytest.mark.asyncio
async def test_pipeline_rest_ops_create_list_get_update_and_cross_actor_visibility(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="pipeline-test-founder")
    _other_actor_id, other_key = _insert_actor_with_key(
        conn,
        name="pipeline-test-observer",
    )
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    headers = _auth_headers(key)

    create_response = await async_client.post(
        "/pipelines",
        headers=headers,
        json={
            "project_id": str(project_id),
            "name": "hotfix-2026-04-28",
        },
    )
    assert create_response.status_code == 200
    created = CreatePipelineResponse.model_validate(create_response.json())
    assert created.pipeline.project_id == project_id
    assert created.pipeline.name == "hotfix-2026-04-28"
    assert created.pipeline.instantiated_from_workflow_id is None
    assert created.pipeline.instantiated_from_workflow_version is None
    assert created.pipeline.created_by_actor_id == actor_id
    assert created.pipeline.created_at.tzinfo == UTC
    assert _pipeline_workflow_links(conn, created.pipeline.id) == (None, None)

    list_response = await async_client.get(
        "/pipelines",
        headers=headers,
        params={"limit": 200},
    )
    assert list_response.status_code == 200
    listed = ListPipelinesResponse.model_validate(list_response.json())
    assert created.pipeline.id in {pipeline.id for pipeline in listed.pipelines}

    get_response = await async_client.get(
        f"/pipelines/{created.pipeline.id}",
        headers=headers,
    )
    assert get_response.status_code == 200
    fetched = GetPipelineResponse.model_validate(get_response.json())
    assert fetched.pipeline == created.pipeline

    update_response = await async_client.patch(
        f"/pipelines/{created.pipeline.id}",
        headers=headers,
        json={"name": "hotfix-2026-04-28-updated"},
    )
    assert update_response.status_code == 200
    updated = UpdatePipelineResponse.model_validate(update_response.json())
    assert updated.pipeline.id == created.pipeline.id
    assert updated.pipeline.project_id == created.pipeline.project_id
    assert updated.pipeline.name == "hotfix-2026-04-28-updated"

    other_list_response = await async_client.get(
        "/pipelines",
        headers=_auth_headers(other_key),
        params={"limit": 200},
    )
    assert other_list_response.status_code == 200
    other_listed = ListPipelinesResponse.model_validate(other_list_response.json())
    assert created.pipeline.id in {pipeline.id for pipeline in other_listed.pipelines}

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_pipeline",
        "update_pipeline",
    ]
    for row in audit_rows:
        assert row["target_kind"] == "pipeline"
        assert row["target_id"] == str(created.pipeline.id)
        assert row["error_code"] is None
        assert row["response_payload"] is not None


@pytest.mark.asyncio
async def test_create_pipeline_project_not_found_returns_404_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="pipeline-test-founder")
    headers = _auth_headers(key)
    missing_project = uuid.UUID("33333333-3333-4333-8333-333333333333")

    create_response = await async_client.post(
        "/pipelines",
        headers=headers,
        json={
            "project_id": str(missing_project),
            "name": "missing-project-pipeline",
        },
    )

    assert create_response.status_code == 404
    assert create_response.json() == {"error": "project_not_found"}
    assert _audit_rows(conn) == [
        {
            "op": "create_pipeline",
            "target_kind": "pipeline",
            "target_id": None,
            "request_payload": {
                "project_id": str(missing_project),
                "name": "missing-project-pipeline",
            },
            "response_payload": {"error": "project_not_found"},
            "error_code": "project_not_found",
        }
    ]


@pytest.mark.asyncio
async def test_update_pipeline_rejects_project_id_write_with_400_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="pipeline-test-founder")
    headers = _auth_headers(key)
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = _insert_pipeline(
        conn,
        project_id=project_id,
        name="immutable-project-pipeline",
        created_by_actor_id=actor_id,
    )

    update_response = await async_client.patch(
        f"/pipelines/{pipeline_id}",
        headers=headers,
        json={
            "name": "renamed-pipeline",
            "project_id": str(project_id),
        },
    )

    assert update_response.status_code == 400
    assert update_response.json() == {"error": "project_id_immutable"}
    assert _audit_rows(conn) == [
        {
            "op": "update_pipeline",
            "target_kind": "pipeline",
            "target_id": str(pipeline_id),
            "request_payload": {
                "pipeline_id": str(pipeline_id),
                "name": "renamed-pipeline",
                "project_id": str(project_id),
            },
            "response_payload": {"error": "project_id_immutable"},
            "error_code": "project_id_immutable",
        }
    ]


@pytest.mark.asyncio
async def test_pipeline_not_found_get_and_update_failures(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="pipeline-test-founder")
    headers = _auth_headers(key)
    missing_pipeline = uuid.UUID("44444444-4444-4444-8444-444444444444")

    get_response = await async_client.get(
        f"/pipelines/{missing_pipeline}",
        headers=headers,
    )
    update_response = await async_client.patch(
        f"/pipelines/{missing_pipeline}",
        headers=headers,
        json={"name": "missing"},
    )

    assert get_response.status_code == 404
    assert get_response.json() == {"error": "pipeline_not_found"}
    assert update_response.status_code == 404
    assert update_response.json() == {"error": "pipeline_not_found"}
    assert _audit_rows(conn) == [
        {
            "op": "update_pipeline",
            "target_kind": "pipeline",
            "target_id": str(missing_pipeline),
            "request_payload": {
                "pipeline_id": str(missing_pipeline),
                "name": "missing",
            },
            "response_payload": {"error": "pipeline_not_found"},
            "error_code": "pipeline_not_found",
        }
    ]
