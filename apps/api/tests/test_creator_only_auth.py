from __future__ import annotations

import httpx
import pytest
from _artifact_test_support import insert_component, insert_objective
from _dl_test_support import insert_learning
from _jobs_test_support import audit_rows, auth_headers, insert_actor_with_key
from _submit_job_test_support import DB_SKIP, fixture_project
from _submit_job_test_support import (
    async_client as async_client,  # noqa: F401
)
from _submit_job_test_support import (
    conn as conn,  # noqa: F401
)
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import (
    isolated_schema as isolated_schema,  # noqa: F401
)
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_edit_learning_rejects_cross_actor_with_details_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    owner_id, _owner_key, project_id, _pipeline_id = fixture_project(conn)
    other_actor_id, other_key = insert_actor_with_key(conn, name="job-test-other")
    learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=owner_id,
        title="Owned learning",
    )

    response = await async_client.patch(
        f"/learnings/{learning_id}",
        headers=auth_headers(other_key),
        json={"title": "Cross actor update"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "learning_edit_forbidden",
        "details": {
            "actor_id": str(other_actor_id),
            "created_by_actor_id": str(owner_id),
            "learning_id": str(learning_id),
        },
    }
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "edit_learning"
    assert rows[-1]["target_id"] == str(learning_id)
    assert rows[-1]["error_code"] == "learning_edit_forbidden"
    assert rows[-1]["response_payload"]["details"] == {
        "actor_id": str(other_actor_id),
        "created_by_actor_id": str(owner_id),
        "learning_id": str(learning_id),
    }


@pytest.mark.asyncio
async def test_update_objective_rejects_cross_actor_with_details_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    owner_id, _owner_key, project_id, _pipeline_id = fixture_project(conn)
    other_actor_id, other_key = insert_actor_with_key(conn, name="job-test-other")
    objective_id = insert_objective(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=owner_id,
        statement="Owned objective",
    )

    response = await async_client.patch(
        f"/objectives/{objective_id}",
        headers=auth_headers(other_key),
        json={"statement": "Cross actor update"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "objective_update_forbidden",
        "details": {
            "actor_id": str(other_actor_id),
            "created_by_actor_id": str(owner_id),
            "objective_id": str(objective_id),
        },
    }
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "update_objective"
    assert rows[-1]["target_id"] == str(objective_id)
    assert rows[-1]["error_code"] == "objective_update_forbidden"


@pytest.mark.asyncio
async def test_update_component_rejects_cross_actor_with_details_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    owner_id, _owner_key, project_id, _pipeline_id = fixture_project(conn)
    other_actor_id, other_key = insert_actor_with_key(conn, name="job-test-other")
    component_id = insert_component(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=owner_id,
        name="Owned component",
        access_path="mcp__owned__tool",
    )

    response = await async_client.patch(
        f"/components/{component_id}",
        headers=auth_headers(other_key),
        json={"name": "Cross actor update"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "component_update_forbidden",
        "details": {
            "actor_id": str(other_actor_id),
            "created_by_actor_id": str(owner_id),
            "component_id": str(component_id),
        },
    }
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "update_component"
    assert rows[-1]["target_id"] == str(component_id)
    assert rows[-1]["error_code"] == "component_update_forbidden"
