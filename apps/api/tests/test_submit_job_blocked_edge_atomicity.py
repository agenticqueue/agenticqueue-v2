import httpx
import pytest
from _submit_job_test_support import (
    CONTRACT,
    DB_SKIP,
    audit_rows,
    auth_headers,
    blocked_payload,
    claimed_job,
    decision_rows,
    gated_edge_count,
    insert_gated_edge,
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
from aq_api.models import SubmitJobBlockedRequest
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_submit_job_blocked_duplicate_edge_rolls_back_and_audits_denial(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id, job_id = claimed_job(conn)
    gated_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="already gated job",
        contract=CONTRACT,
    )
    insert_gated_edge(conn, from_job_id=job_id, to_job_id=gated_job_id)
    payload = blocked_payload(gated_job_id, decisions_made=[], learnings=[])

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["error"] == "gated_on_already_exists"
    assert response.json()["details"]["gated_on_job_id"] == str(gated_job_id)
    assert job_row(conn, job_id)["state"] == "in_progress"
    assert gated_edge_count(conn, from_job_id=job_id, to_job_id=gated_job_id) == 1
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    rows = audit_rows(conn)
    assert rows == [
        {
            "op": "submit_job",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {"job_id": str(job_id), **payload},
            "response_payload": {
                "error": "gated_on_already_exists",
                "details": {"gated_on_job_id": str(gated_job_id)},
            },
            "error_code": "gated_on_already_exists",
        }
    ]


@pytest.mark.asyncio
async def test_submit_job_blocked_unexpected_edge_failure_rolls_back_everything(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service
    from aq_api._db import SessionLocal

    actor_id, _key, project_id, pipeline_id, job_id = claimed_job(conn)
    gated_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="gating job",
        contract=CONTRACT,
    )

    async def fail_edge_insert(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic edge insert failure")

    monkeypatch.setattr(
        submit_service,
        "_insert_gated_on_edge",
        fail_edge_insert,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="synthetic edge insert failure"):
        async with SessionLocal() as session:
            await submit_service.submit_job(
                session,
                job_id=job_id,
                request=SubmitJobBlockedRequest.model_validate(
                    blocked_payload(gated_job_id)
                ),
                actor_id=actor_id,
            )

    assert job_row(conn, job_id)["state"] == "in_progress"
    assert gated_edge_count(conn, from_job_id=job_id, to_job_id=gated_job_id) == 0
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    assert audit_rows(conn) == []
