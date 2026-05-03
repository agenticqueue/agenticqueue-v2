import httpx
import pytest
from _submit_job_test_support import (
    CONTRACT,
    DB_SKIP,
    audit_rows,
    auth_headers,
    blocked_payload,
    claimed_job,
    insert_job,
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
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_submit_job_blocked_rejects_dod_results_at_pydantic_boundary(
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
    payload = blocked_payload(gated_job_id, dod_results=[])

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 422
    assert audit_rows(conn) == []
