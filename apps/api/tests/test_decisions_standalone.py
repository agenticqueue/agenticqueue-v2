from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from _dl_test_support import decision_row, insert_decision
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


def _created_counts(conn: Connection[tuple[object, ...]]) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT attached_to_kind, count(*)
            FROM decisions
            GROUP BY attached_to_kind
            ORDER BY attached_to_kind
            """
        )
        rows = cursor.fetchall()
    return {str(kind): int(count) for kind, count in rows}


@pytest.mark.asyncio
async def test_create_decision_accepts_project_pipeline_and_job_targets(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="decision target",
    )

    for kind, target_id in (
        ("project", project_id),
        ("pipeline", pipeline_id),
        ("job", job_id),
    ):
        response = await async_client.post(
            "/decisions",
            headers=auth_headers(key),
            json={
                "attached_to_kind": kind,
                "attached_to_id": str(target_id),
                "title": f"{kind} decision",
                "statement": f"{kind} decision statement",
                "rationale": f"{kind} rationale",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        decision = payload["decision"]
        UUID(decision["id"])
        assert decision["attached_to_kind"] == kind
        assert decision["attached_to_id"] == str(target_id)
        assert decision["created_by_actor_id"] == str(actor_id)

    assert _created_counts(conn) == {"job": 1, "pipeline": 1, "project": 1}
    assert [row["op"] for row in audit_rows(conn)] == ["create_decision"] * 3


@pytest.mark.asyncio
async def test_create_decision_missing_target_is_audited_404_with_details(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = fixture_project(conn)
    missing_id = uuid4()

    response = await async_client.post(
        "/decisions",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "project",
            "attached_to_id": str(missing_id),
            "title": "Missing target",
            "statement": "Missing target should reject.",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": "attached_target_not_found",
        "details": {
            "attached_to_kind": "project",
            "attached_to_id": str(missing_id),
        },
    }
    rows = audit_rows(conn)
    assert len(rows) == 1
    assert rows[0]["op"] == "create_decision"
    assert rows[0]["error_code"] == "attached_target_not_found"
    assert rows[0]["response_payload"]["details"] == {
        "attached_to_kind": "project",
        "attached_to_id": str(missing_id),
    }


@pytest.mark.asyncio
async def test_get_decision_returns_decision_with_empty_visuals_array(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    decision_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="Gettable decision",
    )

    response = await async_client.get(
        f"/decisions/{decision_id}",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    assert response.json()["decision"]["id"] == str(decision_id)
    assert response.json()["visuals"] == []
    assert decision_row(conn, decision_id)["deactivated_at"] is None


@pytest.mark.asyncio
async def test_list_decisions_filters_attachment_actor_since_and_deactivation(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    project_decision_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project decision",
        created_at_offset_seconds=-30,
    )
    pipeline_decision_id = insert_decision(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="pipeline decision",
        created_at_offset_seconds=-10,
    )
    insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="inactive decision",
        created_at_offset_seconds=-5,
        deactivated=True,
    )

    response = await async_client.get(
        "/decisions",
        headers=auth_headers(key),
        params={
            "attached_to_kind": "project",
            "attached_to_id": str(project_id),
            "actor_id": str(actor_id),
            "include_deactivated": False,
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        str(project_decision_id)
    ]
    assert response.json()["next_cursor"] is None

    response = await async_client.get(
        "/decisions",
        headers=auth_headers(key),
        params={"limit": 10},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        str(pipeline_decision_id),
        str(project_decision_id),
    ]
