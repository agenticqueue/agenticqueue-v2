from __future__ import annotations

import httpx
import pytest
from _dl_test_support import insert_decision, insert_learning
from _jobs_test_support import auth_headers, insert_job
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


def _ids(items: list[dict[str, object]]) -> list[str]:
    return [str(item["id"]) for item in items]


@pytest.mark.asyncio
async def test_get_project_populates_direct_decisions_and_learnings(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    decision_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project decision",
    )
    learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project learning",
    )

    response = await async_client.get(
        f"/projects/{project_id}",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    payload = response.json()
    assert _ids(payload["decisions"]["direct"]) == [str(decision_id)]
    assert payload["decisions"]["inherited"] == []
    assert _ids(payload["learnings"]["direct"]) == [str(learning_id)]
    assert payload["learnings"]["inherited"] == []


@pytest.mark.asyncio
async def test_get_pipeline_inherits_project_decisions_and_learnings(
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
    )
    pipeline_decision_id = insert_decision(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="pipeline decision",
    )
    project_learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project learning",
    )
    pipeline_learning_id = insert_learning(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="pipeline learning",
    )

    response = await async_client.get(
        f"/pipelines/{pipeline_id}",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    payload = response.json()
    assert _ids(payload["decisions"]["direct"]) == [str(pipeline_decision_id)]
    assert _ids(payload["decisions"]["inherited"]) == [str(project_decision_id)]
    assert _ids(payload["learnings"]["direct"]) == [str(pipeline_learning_id)]
    assert _ids(payload["learnings"]["inherited"]) == [str(project_learning_id)]


@pytest.mark.asyncio
async def test_get_job_inherits_pipeline_and_project_decisions_and_learnings(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="inheritance target",
    )
    project_decision_id = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project decision",
    )
    pipeline_decision_id = insert_decision(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="pipeline decision",
    )
    job_decision_id = insert_decision(
        conn,
        attached_to_kind="job",
        attached_to_id=job_id,
        created_by_actor_id=actor_id,
        title="job decision",
    )
    project_learning_id = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="project learning",
    )
    pipeline_learning_id = insert_learning(
        conn,
        attached_to_kind="pipeline",
        attached_to_id=pipeline_id,
        created_by_actor_id=actor_id,
        title="pipeline learning",
    )
    job_learning_id = insert_learning(
        conn,
        attached_to_kind="job",
        attached_to_id=job_id,
        created_by_actor_id=actor_id,
        title="job learning",
    )

    response = await async_client.get(
        f"/jobs/{job_id}",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    payload = response.json()
    assert _ids(payload["decisions"]["direct"]) == [str(job_decision_id)]
    assert _ids(payload["decisions"]["inherited"]) == [
        str(pipeline_decision_id),
        str(project_decision_id),
    ]
    assert _ids(payload["learnings"]["direct"]) == [str(job_learning_id)]
    assert _ids(payload["learnings"]["inherited"]) == [
        str(pipeline_learning_id),
        str(project_learning_id),
    ]


@pytest.mark.asyncio
async def test_resolve_attached_chain_helper_is_importable_and_resolves_job(
    conn: Connection[tuple[object, ...]],
) -> None:
    from aq_api import _db
    from aq_api.services._inheritance import _resolve_attached_chain

    actor_id, _key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="helper target",
    )

    async with _db.SessionLocal() as session:
        chain = await _resolve_attached_chain(
            session,
            entity_kind="job",
            entity_id=job_id,
        )

    assert chain is not None
    assert chain.project_id == project_id
    assert chain.pipeline_id == pipeline_id
    assert chain.job_id == job_id
