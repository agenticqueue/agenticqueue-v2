from uuid import uuid4

import httpx
import pytest
from _submit_job_test_support import (
    CONTRACT,
    DB_SKIP,
    assert_claim_cleared,
    assert_inline_dl_created,
    audit_rows,
    auth_headers,
    blocked_payload,
    claimed_job,
    decision_rows,
    fixture_project,
    gated_edge_count,
    insert_job,
    job_row,
    learning_rows,
)
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
from aq_api.models import SubmitJobResponse
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_submit_job_blocked_inserts_gated_on_edge_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id, job_id = claimed_job(conn)
    gated_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="gating job",
        contract=CONTRACT,
    )
    payload = blocked_payload(gated_job_id)

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 200
    body = SubmitJobResponse.model_validate(response.json())
    assert body.job.state == "blocked"
    assert body.created_gated_on_edge is True
    assert gated_edge_count(conn, from_job_id=job_id, to_job_id=gated_job_id) == 1
    assert_claim_cleared(job_row(conn, job_id))
    assert_inline_dl_created(conn, job_id=job_id, actor_id=actor_id, response=body)
    assert audit_rows(conn) == [
        {
            "op": "submit_job",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {"job_id": str(job_id), **payload},
            "response_payload": {
                "outcome": "blocked",
                "created_decisions": [str(value) for value in body.created_decisions],
                "created_learnings": [str(value) for value in body.created_learnings],
                "created_gated_on_edge": True,
            },
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["not_found", "cross_project", "self"],
)
async def test_submit_job_blocked_rejects_invalid_gating_job(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    case: str,
) -> None:
    actor_id, key, project_id, pipeline_id, job_id = claimed_job(conn)
    if case == "not_found":
        gated_job_id = uuid4()
    elif case == "cross_project":
        other_actor_id, _other_key, other_project_id, other_pipeline_id = (
            fixture_project(conn)
        )
        gated_job_id = insert_job(
            conn,
            pipeline_id=other_pipeline_id,
            project_id=other_project_id,
            created_by_actor_id=other_actor_id,
            title="cross project gate",
            contract=CONTRACT,
        )
    else:
        gated_job_id = job_id

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=blocked_payload(gated_job_id, decisions_made=[], learnings=[]),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "gated_on_invalid"
    assert response.json()["details"]["rule"] == case
    assert job_row(conn, job_id)["state"] == "in_progress"
    assert gated_edge_count(conn, from_job_id=job_id, to_job_id=gated_job_id) == 0
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    rows = audit_rows(conn)
    assert rows[0]["error_code"] == "gated_on_invalid"
    assert rows[0]["response_payload"]["details"]["rule"] == case
    assert rows[0]["target_id"] == str(job_id)
