from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from _artifact_test_support import insert_objective, objective_row
from _jobs_test_support import audit_rows, auth_headers
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
from sqlalchemy.exc import IntegrityError

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_create_objective_accepts_project_and_pipeline_targets(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)

    for kind, target_id in (("project", project_id), ("pipeline", pipeline_id)):
        response = await async_client.post(
            "/objectives",
            headers=auth_headers(key),
            json={
                "attached_to_kind": kind,
                "attached_to_id": str(target_id),
                "statement": f"{kind} objective",
                "metric": "coverage",
                "target_value": "100%",
            },
        )

        assert response.status_code == 200
        objective = response.json()["objective"]
        UUID(objective["id"])
        assert objective["attached_to_kind"] == kind
        assert objective["attached_to_id"] == str(target_id)
        assert objective["created_by_actor_id"] == str(actor_id)

    assert [row["op"] for row in audit_rows(conn)] == ["create_objective"] * 2


@pytest.mark.asyncio
async def test_create_objective_rejects_missing_target_and_required_fields(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = fixture_project(conn)
    missing_id = uuid4()

    response = await async_client.post(
        "/objectives",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "project",
            "attached_to_id": str(missing_id),
            "statement": "missing target",
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
    assert audit_rows(conn)[-1]["error_code"] == "attached_target_not_found"

    missing_statement = await async_client.post(
        "/objectives",
        headers=auth_headers(key),
        json={"attached_to_kind": "project", "attached_to_id": str(missing_id)},
    )
    assert missing_statement.status_code == 422
    assert missing_statement.json()["detail"][0]["loc"][-1] == "statement"


@pytest.mark.asyncio
async def test_objective_attached_to_kind_rejected_by_pydantic_and_db_check(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)

    response = await async_client.post(
        "/objectives",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "job",
            "attached_to_id": str(project_id),
            "statement": "invalid target kind",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "attached_to_kind"

    from aq_api import _db
    from aq_api.models.db import Objective as DbObjective

    async with _db.SessionLocal() as session:
        session.add(
            DbObjective(
                attached_to_kind="job",
                attached_to_id=project_id,
                statement="db check bypass",
                created_by_actor_id=actor_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_get_and_update_objective_use_creator_allowlist_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    objective_id = insert_objective(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        statement="Original objective",
        metric="original",
        target_value="0",
    )

    get_response = await async_client.get(
        f"/objectives/{objective_id}",
        headers=auth_headers(key),
    )
    assert get_response.status_code == 200
    assert get_response.json()["objective"]["id"] == str(objective_id)

    update_response = await async_client.patch(
        f"/objectives/{objective_id}",
        headers=auth_headers(key),
        json={
            "statement": "Updated objective",
            "metric": None,
            "target_value": "done",
            "due_at": None,
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()["objective"]
    assert payload["statement"] == "Updated objective"
    assert payload["metric"] is None
    assert payload["target_value"] == "done"
    stored = objective_row(conn, objective_id)
    assert stored["statement"] == "Updated objective"
    assert stored["metric"] is None
    assert stored["target_value"] == "done"
    assert audit_rows(conn)[-1]["op"] == "update_objective"

    disallowed = await async_client.patch(
        f"/objectives/{objective_id}",
        headers=auth_headers(key),
        json={"attached_to_kind": "pipeline"},
    )
    assert disallowed.status_code == 422
