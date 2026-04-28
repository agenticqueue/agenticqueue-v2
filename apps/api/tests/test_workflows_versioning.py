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
from aq_api.models import (
    CreateWorkflowResponse,
    GetWorkflowResponse,
    ListWorkflowsResponse,
    UpdateWorkflowResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "workflow-test-"
WORKFLOW_SLUG_PREFIX = "workflow-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live workflow tests",
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
        project_slug_prefix=WORKFLOW_SLUG_PREFIX,
    )


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_workflow_contract_{uuid.uuid4().hex}"
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
                f"workflow-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _contract_profile_id(conn: Connection[tuple[object, ...]]) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM contract_profiles WHERE name = 'coding-task' LIMIT 1"
        )
        row = cursor.fetchone()
    assert row is not None
    profile_id = row[0]
    assert isinstance(profile_id, UUID)
    return str(profile_id)


def _workflow_steps(
    conn: Connection[tuple[object, ...]],
    workflow_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, workflow_id, name, ordinal, default_contract_profile_id,
                   step_edges
            FROM workflow_steps
            WHERE workflow_id = %s
            ORDER BY ordinal ASC
            """,
            (workflow_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "workflow_id": row[1],
            "name": row[2],
            "ordinal": row[3],
            "default_contract_profile_id": row[4],
            "step_edges": row[5],
        }
        for row in rows
    ]


def _workflow_rows_by_slug(
    conn: Connection[tuple[object, ...]],
    slug: str,
) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, slug, name, version, is_archived,
                   created_by_actor_id, supersedes_workflow_id
            FROM workflows
            WHERE slug = %s
            ORDER BY version ASC
            """,
            (slug,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "slug": row[1],
            "name": row[2],
            "version": row[3],
            "is_archived": row[4],
            "created_by_actor_id": row[5],
            "supersedes_workflow_id": row[6],
        }
        for row in rows
    ]


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


def _create_steps(profile_id: str) -> list[dict[str, object]]:
    return [
        {
            "name": "scope",
            "ordinal": 1,
            "default_contract_profile_id": profile_id,
            "step_edges": {"phase": "discovery"},
        },
        {
            "name": "build",
            "ordinal": 2,
            "default_contract_profile_id": profile_id,
            "step_edges": {"after": ["scope"]},
        },
        {
            "name": "verify",
            "ordinal": 3,
            "default_contract_profile_id": profile_id,
            "step_edges": {"after": ["build"]},
        },
    ]


def _update_steps(profile_id: str) -> list[dict[str, object]]:
    return [
        {
            "name": "scope",
            "ordinal": 1,
            "default_contract_profile_id": profile_id,
            "step_edges": {},
        },
        {
            "name": "design",
            "ordinal": 2,
            "default_contract_profile_id": profile_id,
            "step_edges": {"after": ["scope"]},
        },
        {
            "name": "build",
            "ordinal": 3,
            "default_contract_profile_id": profile_id,
            "step_edges": {"after": ["design"]},
        },
        {
            "name": "verify",
            "ordinal": 4,
            "default_contract_profile_id": profile_id,
            "step_edges": {"after": ["build"]},
        },
    ]


@pytest.mark.asyncio
async def test_workflow_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'
    workflow_id = "11111111-1111-4111-8111-111111111111"

    responses = [
        await async_client.post(
            "/workflows",
            json={"name": "Workflow Ops", "slug": "workflow-ops", "steps": []},
        ),
        await async_client.get("/workflows"),
        await async_client.get(f"/workflows/{workflow_id}"),
        await async_client.patch(
            f"/workflows/{workflow_id}",
            json={"name": "Renamed", "steps": []},
        ),
        await async_client.post("/workflows/workflow-ops/archive", json={}),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.content == expected


@pytest.mark.asyncio
async def test_update_workflow_creates_new_version_steps_and_preserves_history(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="workflow-test-founder")
    headers = _auth_headers(key)
    profile_id = _contract_profile_id(conn)
    slug = "workflow-test-versioning"

    create_response = await async_client.post(
        "/workflows",
        headers=headers,
        json={"name": "Ship A Thing", "slug": slug, "steps": _create_steps(profile_id)},
    )
    assert create_response.status_code == 200
    created = CreateWorkflowResponse.model_validate(create_response.json())
    assert created.workflow.slug == slug
    assert created.workflow.version == 1
    assert created.workflow.created_by_actor_id == actor_id
    assert created.workflow.supersedes_workflow_id is None
    assert [step.name for step in created.workflow.steps] == [
        "scope",
        "build",
        "verify",
    ]

    original_steps = _workflow_steps(conn, created.workflow.id)

    update_response = await async_client.patch(
        f"/workflows/{created.workflow.id}",
        headers=headers,
        json={"name": "Ship A Thing v2", "steps": _update_steps(profile_id)},
    )
    assert update_response.status_code == 200
    updated = UpdateWorkflowResponse.model_validate(update_response.json())
    assert updated.workflow.id != created.workflow.id
    assert updated.workflow.slug == slug
    assert updated.workflow.name == "Ship A Thing v2"
    assert updated.workflow.version == 2
    assert updated.workflow.supersedes_workflow_id == created.workflow.id
    assert updated.workflow.created_by_actor_id == actor_id
    assert [step.name for step in updated.workflow.steps] == [
        "scope",
        "design",
        "build",
        "verify",
    ]

    rows = _workflow_rows_by_slug(conn, slug)
    assert [(row["id"], row["version"]) for row in rows] == [
        (created.workflow.id, 1),
        (updated.workflow.id, 2),
    ]
    assert rows[0]["name"] == "Ship A Thing"
    assert rows[0]["is_archived"] is False
    assert rows[1]["supersedes_workflow_id"] == created.workflow.id

    after_original_steps = _workflow_steps(conn, created.workflow.id)
    new_steps = _workflow_steps(conn, updated.workflow.id)
    assert after_original_steps == original_steps
    assert [step["name"] for step in new_steps] == [
        "scope",
        "design",
        "build",
        "verify",
    ]
    assert {step["id"] for step in original_steps}.isdisjoint(
        {step["id"] for step in new_steps}
    )
    assert all(step["default_contract_profile_id"] is not None for step in new_steps)

    old_get_response = await async_client.get(
        f"/workflows/{created.workflow.id}",
        headers=headers,
    )
    assert old_get_response.status_code == 200
    old_get = GetWorkflowResponse.model_validate(old_get_response.json())
    assert old_get.workflow.id == created.workflow.id
    assert old_get.workflow.version == 1
    assert [step.name for step in old_get.workflow.steps] == [
        "scope",
        "build",
        "verify",
    ]

    list_response = await async_client.get(
        "/workflows",
        headers=headers,
        params={"limit": 200},
    )
    assert list_response.status_code == 200
    listed = ListWorkflowsResponse.model_validate(list_response.json())
    assert {created.workflow.id, updated.workflow.id}.issubset(
        {workflow.id for workflow in listed.workflows}
    )

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_workflow",
        "update_workflow",
    ]
    assert audit_rows[0]["target_kind"] == "workflow"
    assert audit_rows[0]["target_id"] == str(created.workflow.id)
    assert audit_rows[1]["target_kind"] == "workflow"
    assert audit_rows[1]["target_id"] == str(updated.workflow.id)
    assert audit_rows[1]["error_code"] is None


@pytest.mark.asyncio
async def test_stale_update_returns_409_workflow_not_latest_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="workflow-test-founder")
    headers = _auth_headers(key)
    profile_id = _contract_profile_id(conn)

    create_response = await async_client.post(
        "/workflows",
        headers=headers,
        json={
            "name": "Stale Update",
            "slug": "workflow-test-stale",
            "steps": _create_steps(profile_id),
        },
    )
    assert create_response.status_code == 200
    created = CreateWorkflowResponse.model_validate(create_response.json())

    update_response = await async_client.patch(
        f"/workflows/{created.workflow.id}",
        headers=headers,
        json={"name": "Stale Update v2", "steps": _update_steps(profile_id)},
    )
    assert update_response.status_code == 200

    stale_response = await async_client.patch(
        f"/workflows/{created.workflow.id}",
        headers=headers,
        json={"name": "Should Not Land", "steps": _update_steps(profile_id)},
    )

    assert stale_response.status_code == 409
    assert stale_response.json() == {"error": "workflow_not_latest"}
    assert len(_workflow_rows_by_slug(conn, "workflow-test-stale")) == 2

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_workflow",
        "update_workflow",
        "update_workflow",
    ]
    stale_audit = audit_rows[-1]
    assert stale_audit["target_kind"] == "workflow"
    assert stale_audit["target_id"] == str(created.workflow.id)
    assert stale_audit["error_code"] == "workflow_not_latest"
    assert stale_audit["response_payload"] == {"error": "workflow_not_latest"}
