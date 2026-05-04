from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from _dl_test_support import insert_learning, learning_row
from _jobs_test_support import audit_rows, auth_headers, insert_job
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
async def test_submit_learning_accepts_project_pipeline_and_job_targets(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="learning target",
    )

    for kind, target_id in (
        ("project", project_id),
        ("pipeline", pipeline_id),
        ("job", job_id),
    ):
        response = await async_client.post(
            "/learnings",
            headers=auth_headers(key),
            json={
                "attached_to_kind": kind,
                "attached_to_id": str(target_id),
                "title": f"{kind} learning",
                "statement": f"{kind} learning statement",
                "context": f"{kind} context",
            },
        )

        assert response.status_code == 200
        learning = response.json()["learning"]
        UUID(learning["id"])
        assert learning["attached_to_kind"] == kind
        assert learning["attached_to_id"] == str(target_id)
        assert learning["created_by_actor_id"] == str(actor_id)

    assert [row["op"] for row in audit_rows(conn)] == ["submit_learning"] * 3


@pytest.mark.asyncio
async def test_submit_learning_missing_target_is_audited_404_with_details(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = fixture_project(conn)
    missing_id = uuid4()

    response = await async_client.post(
        "/learnings",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "pipeline",
            "attached_to_id": str(missing_id),
            "title": "Missing target",
            "statement": "Missing target should reject.",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": "attached_target_not_found",
        "details": {
            "attached_to_kind": "pipeline",
            "attached_to_id": str(missing_id),
        },
    }
    rows = audit_rows(conn)
    assert len(rows) == 1
    assert rows[0]["op"] == "submit_learning"
    assert rows[0]["error_code"] == "attached_target_not_found"


@pytest.mark.asyncio
async def test_get_learning_returns_learning_with_empty_visuals_array(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Gettable learning",
    )

    response = await async_client.get(
        f"/learnings/{learning_id}",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    assert response.json()["learning"]["id"] == str(learning_id)
    assert response.json()["visuals"] == []


@pytest.mark.asyncio
async def test_edit_learning_updates_creator_owned_fields_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Original learning",
        statement="Original statement",
        context="Original context",
    )

    response = await async_client.patch(
        f"/learnings/{learning_id}",
        headers=auth_headers(key),
        json={
            "title": "Edited learning",
            "statement": "Edited statement",
            "context": None,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["learning"]["title"] == "Edited learning"
    assert payload["learning"]["statement"] == "Edited statement"
    assert payload["learning"]["context"] is None
    stored = learning_row(conn, learning_id)
    assert stored["title"] == "Edited learning"
    assert stored["statement"] == "Edited statement"
    assert stored["context"] is None
    rows = audit_rows(conn)
    assert rows[-1]["op"] == "edit_learning"
    assert rows[-1]["target_id"] == str(learning_id)


@pytest.mark.asyncio
async def test_list_learnings_filters_attachment_actor_and_deactivation(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    active_project_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="active project learning",
        created_at_offset_seconds=-20,
    )
    active_pipeline_id = insert_learning(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="active pipeline learning",
        created_at_offset_seconds=-10,
    )
    inactive_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="inactive learning",
        created_at_offset_seconds=-5,
        deactivated=True,
    )

    response = await async_client.get(
        "/learnings",
        headers=auth_headers(key),
        params={"limit": 10},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        str(active_pipeline_id),
        str(active_project_id),
    ]

    response = await async_client.get(
        "/learnings",
        headers=auth_headers(key),
        params={
            "attached_to_kind": "project",
            "attached_to_id": str(project_id),
            "actor_id": str(actor_id),
            "include_deactivated": True,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        str(inactive_id),
        str(active_project_id),
    ]
