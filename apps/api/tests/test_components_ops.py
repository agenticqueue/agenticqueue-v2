from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from _artifact_test_support import component_row, insert_component
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
async def test_create_component_accepts_project_and_pipeline_targets(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)

    for kind, target_id in (("project", project_id), ("pipeline", pipeline_id)):
        response = await async_client.post(
            "/components",
            headers=auth_headers(key),
            json={
                "attached_to_kind": kind,
                "attached_to_id": str(target_id),
                "name": f"{kind} component",
                "purpose": f"{kind} purpose",
                "access_path": f"mcp__{kind}__tool",
            },
        )

        assert response.status_code == 200
        component = response.json()["component"]
        UUID(component["id"])
        assert component["attached_to_kind"] == kind
        assert component["attached_to_id"] == str(target_id)
        assert component["created_by_actor_id"] == str(actor_id)

    assert [row["op"] for row in audit_rows(conn)] == ["create_component"] * 2


@pytest.mark.asyncio
async def test_create_component_rejects_missing_target_and_required_fields(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = fixture_project(conn)
    missing_id = uuid4()

    response = await async_client.post(
        "/components",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "pipeline",
            "attached_to_id": str(missing_id),
            "name": "missing target",
            "access_path": "mcp__missing__tool",
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
    assert audit_rows(conn)[-1]["error_code"] == "attached_target_not_found"

    missing_access_path = await async_client.post(
        "/components",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "project",
            "attached_to_id": str(missing_id),
            "name": "missing access path",
        },
    )
    assert missing_access_path.status_code == 422
    assert missing_access_path.json()["detail"][0]["loc"][-1] == "access_path"


@pytest.mark.asyncio
async def test_component_attached_to_kind_rejected_by_pydantic_and_db_check(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)

    response = await async_client.post(
        "/components",
        headers=auth_headers(key),
        json={
            "attached_to_kind": "job",
            "attached_to_id": str(project_id),
            "name": "invalid target kind",
            "access_path": "mcp__invalid__tool",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "attached_to_kind"

    from aq_api import _db
    from aq_api.models.db import Component as DbComponent

    async with _db.SessionLocal() as session:
        session.add(
            DbComponent(
                attached_to_kind="job",
                attached_to_id=project_id,
                name="db check bypass",
                access_path="mcp__invalid__tool",
                created_by_actor_id=actor_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_get_and_update_component_use_creator_allowlist_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    component_id = insert_component(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        name="Qdrant",
        purpose="Original purpose",
        access_path="mcp__old__tool",
    )

    get_response = await async_client.get(
        f"/components/{component_id}",
        headers=auth_headers(key),
    )
    assert get_response.status_code == 200
    assert get_response.json()["component"]["id"] == str(component_id)

    update_response = await async_client.patch(
        f"/components/{component_id}",
        headers=auth_headers(key),
        json={
            "name": "Qdrant cluster",
            "purpose": None,
            "access_path": "mcp__qdrant__search",
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()["component"]
    assert payload["name"] == "Qdrant cluster"
    assert payload["purpose"] is None
    assert payload["access_path"] == "mcp__qdrant__search"
    stored = component_row(conn, component_id)
    assert stored["name"] == "Qdrant cluster"
    assert stored["purpose"] is None
    assert stored["access_path"] == "mcp__qdrant__search"
    assert audit_rows(conn)[-1]["op"] == "update_component"

    disallowed = await async_client.patch(
        f"/components/{component_id}",
        headers=auth_headers(key),
        json={"created_by_actor_id": str(actor_id)},
    )
    assert disallowed.status_code == 422
