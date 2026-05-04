from __future__ import annotations

import httpx
import pytest
from _dl_test_support import decision_row, insert_decision
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
async def test_supersede_decision_sets_replacement_pointer_and_deactivates_old(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Old decision",
    )
    replacement_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Replacement decision",
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(key),
        json={"replacement_id": str(replacement_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["old_decision"]["id"] == str(old_id)
    assert payload["old_decision"]["deactivated_at"] is not None
    assert payload["replacement_decision"]["id"] == str(replacement_id)
    assert payload["replacement_decision"]["supersedes_decision_id"] == str(old_id)
    assert decision_row(conn, old_id)["deactivated_at"] is not None
    assert decision_row(conn, replacement_id)["supersedes_decision_id"] == old_id
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "supersede_decision"
    assert rows[-1]["target_id"] == str(old_id)


@pytest.mark.asyncio
async def test_supersede_decision_allows_any_active_actor(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, _owner_key, project_id, _pipeline_id = fixture_project(conn)
    _reviewer_id, reviewer_key = insert_actor_with_key(conn, name="job-test-reviewer")
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Old decision",
    )
    replacement_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Replacement decision",
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(reviewer_key),
        json={"replacement_id": str(replacement_id)},
    )

    assert response.status_code == 200
    assert response.json()["replacement_decision"]["supersedes_decision_id"] == str(
        old_id
    )


@pytest.mark.asyncio
async def test_supersede_decision_rejects_self_supersede(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="self old",
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(key),
        json={"replacement_id": str(old_id)},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "self_supersede"
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "supersede_decision"
    assert rows[-1]["error_code"] == "self_supersede"


@pytest.mark.asyncio
async def test_supersede_decision_rejects_scope_mismatch(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="scope old",
    )
    replacement_id = insert_decision(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="scope replacement",
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(key),
        json={"replacement_id": str(replacement_id)},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "supersede_scope_mismatch"
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "supersede_decision"
    assert rows[-1]["error_code"] == "supersede_scope_mismatch"


@pytest.mark.asyncio
async def test_supersede_decision_rejects_deactivated_old_decision(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="old deactivated",
        deactivated=True,
    )
    replacement_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="old active replacement",
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(key),
        json={"replacement_id": str(replacement_id)},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "decision_already_deactivated"
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "supersede_decision"
    assert rows[-1]["error_code"] == "decision_already_deactivated"


@pytest.mark.asyncio
async def test_supersede_decision_rejects_deactivated_replacement_decision(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    old_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="replacement old",
    )
    replacement_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="replacement deactivated",
        deactivated=True,
    )

    response = await async_client.post(
        f"/decisions/{old_id}/supersede",
        headers=auth_headers(key),
        json={"replacement_id": str(replacement_id)},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "decision_already_deactivated"
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "supersede_decision"
    assert rows[-1]["error_code"] == "decision_already_deactivated"
