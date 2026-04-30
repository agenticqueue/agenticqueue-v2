import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from _isolated_schema import (
    connect_in_schema,
    create_async_engine_in_schema,
    create_cap04_schema,
    drop_schema,
    sync_conninfo,
)
from _jobs_test_support import (
    audit_rows,
    auth_headers,
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    job_row,
    truncate_job_state,
)
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.app import app
from aq_api.mcp import create_mcp_server
from aq_api.models import SubmitJobResponse
from fastmcp import Client
from psycopg import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC or not DATABASE_URL,
    reason="DATABASE_URL and DATABASE_URL_SYNC are required for live submit tests",
)

CONTRACT: dict[str, object] = {
    "contract_type": "coding-task",
    "dod_items": [
        {
            "id": "tests-pass",
            "verification_method": "command",
            "evidence_required": "pytest output",
            "acceptance_threshold": "all tests pass",
        },
        {
            "id": "docs-reviewed",
            "verification_method": "review",
            "evidence_required": "review note",
            "acceptance_threshold": "docs checked",
        },
    ],
}


@pytest.fixture()
def isolated_schema() -> Iterator[str]:
    assert DATABASE_URL_SYNC is not None
    conninfo = sync_conninfo(DATABASE_URL_SYNC)
    schema = create_cap04_schema(conninfo, prefix="cap05_submit")
    try:
        yield schema
    finally:
        drop_schema(conninfo, schema)


@pytest.fixture(autouse=True)
async def isolate_async_session_local(
    monkeypatch: pytest.MonkeyPatch,
    isolated_schema: str,
) -> AsyncIterator[None]:
    assert DATABASE_URL is not None
    import aq_api._db as db_module

    isolated_engine = create_async_engine_in_schema(DATABASE_URL, isolated_schema)
    isolated_session_local = async_sessionmaker(isolated_engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(db_module, "SessionLocal", isolated_session_local)
    try:
        yield
    finally:
        await isolated_engine.dispose()


@pytest.fixture()
def conn(isolated_schema: str) -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = sync_conninfo(DATABASE_URL_SYNC)
    with connect_in_schema(conninfo, isolated_schema) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


def _fixture_project(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, str, UUID, UUID]:
    actor_id, key = insert_actor_with_key(conn, name="job-test-submit-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    return actor_id, key, project_id, pipeline_id


def _mark_claimed(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
    *,
    actor_id: UUID,
    state: str = "in_progress",
) -> None:
    claimed_at = datetime.now(UTC)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET state = %s,
                claimed_by_actor_id = %s,
                claimed_at = %s,
                claim_heartbeat_at = %s
            WHERE id = %s
            """,
            (state, actor_id, claimed_at, claimed_at, job_id),
        )


def _claimed_job(
    conn: Connection[tuple[object, ...]],
    *,
    contract: dict[str, object] | None = None,
    title: str = "submit target",
) -> tuple[UUID, str, UUID]:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title=title,
        contract=contract or CONTRACT,
    )
    _mark_claimed(conn, job_id, actor_id=actor_id)
    return actor_id, key, job_id


def _done_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": ["pytest -q apps/api/tests/test_submit_job_done.py"],
                "summary": "targeted tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "no docs touched",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_submit_job_done.py"],
        "verification_summary": "submit done path verified",
        "files_changed": ["apps/api/src/aq_api/services/submit.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-77",
        "decisions_made": [
            {
                "title": "Use submit service",
                "statement": "Submit transitions the Job and records D&L inline.",
                "rationale": "One transaction keeps closeout durable.",
            },
            {
                "title": "Clear claims on submit",
                "statement": "Successful submit clears all claim fields.",
                "rationale": "Terminal jobs must not look lease-held.",
            }
        ],
        "learnings": [
            {
                "title": "Submit shape",
                "statement": "Done submissions require every DoD to pass.",
                "context": "Cap #5 contract validation.",
            },
            {
                "title": "D&L attachment",
                "statement": "Inline decisions and learnings attach to the Job.",
                "context": "Submit is the durable capture point.",
            },
            {
                "title": "Audit payload",
                "statement": "Submit audit response summarizes created row IDs.",
                "context": "Cap #7 can query runs from audit rows later.",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _decision_rows(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, statement,
                   rationale, supersedes_decision_id, created_by_actor_id
            FROM decisions
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY created_at, id
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _learning_rows(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, statement,
                   context, created_by_actor_id
            FROM learnings
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY created_at, id
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


async def _mcp_call(
    actor_id: UUID,
    tool: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    context_token = set_authenticated_actor_id(actor_id)
    try:
        async with Client(create_mcp_server()) as client:
            result = await client.call_tool(tool, arguments)
    finally:
        reset_authenticated_actor_id(context_token)
    assert result.structured_content is not None
    return {
        "structuredContent": result.structured_content,
        "content": [block.model_dump(mode="json") for block in result.content],
    }


@pytest.mark.asyncio
async def test_submit_job_done_transitions_clears_claim_and_audits_success(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, job_id = _claimed_job(conn)
    payload = _done_payload()

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    submit_response = SubmitJobResponse.model_validate(body)
    assert submit_response.job.id == job_id
    assert submit_response.job.state == "done"
    assert submit_response.job.claimed_by_actor_id is None
    assert submit_response.job.claimed_at is None
    assert submit_response.job.claim_heartbeat_at is None
    assert submit_response.created_gated_on_edge is False
    assert len(submit_response.created_decisions) == 2
    assert len(submit_response.created_learnings) == 3
    assert "audit_row_id" not in body

    stored = job_row(conn, job_id)
    assert stored["state"] == "done"
    assert stored["claimed_by_actor_id"] is None
    assert stored["claimed_at"] is None
    assert stored["claim_heartbeat_at"] is None

    decisions = _decision_rows(conn, job_id)
    learnings = _learning_rows(conn, job_id)
    assert {row["id"] for row in decisions} == set(submit_response.created_decisions)
    assert {row["id"] for row in learnings} == set(submit_response.created_learnings)
    decisions_by_title = {row["title"]: row for row in decisions}
    learnings_by_title = {row["title"]: row for row in learnings}
    assert set(decisions_by_title) == {"Use submit service", "Clear claims on submit"}
    assert set(learnings_by_title) == {
        "Submit shape",
        "D&L attachment",
        "Audit payload",
    }
    for row in decisions:
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["created_by_actor_id"] == actor_id
        assert row["supersedes_decision_id"] is None
    for row in learnings:
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["created_by_actor_id"] == actor_id

    assert audit_rows(conn) == [
        {
            "op": "submit_job",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {"job_id": str(job_id), **payload},
            "response_payload": {
                "outcome": "done",
                "created_decisions": [
                    str(value) for value in submit_response.created_decisions
                ],
                "created_learnings": [
                    str(value) for value in submit_response.created_learnings
                ],
                "created_gated_on_edge": False,
            },
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
async def test_submit_job_done_empty_inline_dl_creates_no_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, job_id = _claimed_job(conn)

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(decisions_made=[], learnings=[]),
    )

    assert response.status_code == 200
    body = SubmitJobResponse.model_validate(response.json())
    assert body.created_decisions == []
    assert body.created_learnings == []
    assert _decision_rows(conn, job_id) == []
    assert _learning_rows(conn, job_id) == []


@pytest.mark.asyncio
async def test_submit_job_done_pydantic_rejection_is_not_audited(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, job_id = _claimed_job(conn)
    payload = _done_payload(extra_field="not accepted")

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 422
    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_submit_job_done_missing_job_returns_404_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = _fixture_project(conn)
    missing_job_id = uuid4()

    response = await async_client.post(
        f"/jobs/{missing_job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "job_not_found"}
    assert audit_rows(conn)[0]["target_id"] == str(missing_job_id)
    assert audit_rows(conn)[0]["error_code"] == "job_not_found"


@pytest.mark.asyncio
async def test_submit_job_done_state_check_runs_before_auth_check(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, _key, job_id = _claimed_job(conn)
    _other_actor_id, other_key = insert_actor_with_key(
        conn,
        name="job-test-submit-other",
    )
    _mark_claimed(conn, job_id, actor_id=actor_id, state="done")

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(other_key),
        json=_done_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "job_not_in_progress"}
    assert audit_rows(conn)[0]["error_code"] == "job_not_in_progress"


@pytest.mark.asyncio
async def test_submit_job_done_all_non_in_progress_states_return_409(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    states = [
        "draft",
        "ready",
        "done",
        "failed",
        "blocked",
        "pending_review",
        "cancelled",
    ]

    for state in states:
        job_id = insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            title=f"submit wrong state {state}",
            state=state,
            contract=CONTRACT,
        )
        if state not in {"draft", "ready"}:
            _mark_claimed(conn, job_id, actor_id=actor_id, state=state)

        response = await async_client.post(
            f"/jobs/{job_id}/submit",
            headers=auth_headers(key),
            json=_done_payload(decisions_made=[], learnings=[]),
        )

        assert response.status_code == 409
        assert response.json() == {"error": "job_not_in_progress"}
        assert job_row(conn, job_id)["state"] == state
        assert _decision_rows(conn, job_id) == []
        assert _learning_rows(conn, job_id) == []

    rows = audit_rows(conn)
    assert [row["error_code"] for row in rows] == [
        "job_not_in_progress",
        "job_not_in_progress",
        "job_not_in_progress",
        "job_not_in_progress",
        "job_not_in_progress",
        "job_not_in_progress",
        "job_not_in_progress",
    ]


@pytest.mark.asyncio
async def test_submit_job_done_wrong_claimant_returns_403_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, _key, job_id = _claimed_job(conn)
    _other_actor_id, other_key = insert_actor_with_key(
        conn,
        name="job-test-submit-other",
    )

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(other_key),
        json=_done_payload(),
    )

    assert response.status_code == 403
    assert response.json() == {"error": "submit_forbidden"}
    assert audit_rows(conn)[0]["error_code"] == "submit_forbidden"
    assert job_row(conn, job_id)["state"] == "in_progress"
    assert _decision_rows(conn, job_id) == []
    assert _learning_rows(conn, job_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "rule"),
    [
        (
            _done_payload(
                dod_results=[
                    {
                        "dod_id": "unknown",
                        "status": "passed",
                        "evidence": ["pytest"],
                        "summary": "bad id",
                    },
                    {
                        "dod_id": "docs-reviewed",
                        "status": "not_applicable",
                        "evidence": [],
                        "summary": "docs skipped",
                    },
                ]
            ),
            "dod_id_unknown",
        ),
        (
            _done_payload(
                dod_results=[
                    {
                        "dod_id": "tests-pass",
                        "status": "passed",
                        "evidence": ["pytest"],
                        "summary": "first",
                    },
                    {
                        "dod_id": "tests-pass",
                        "status": "passed",
                        "evidence": ["pytest again"],
                        "summary": "duplicate",
                    },
                ]
            ),
            "duplicate_dod_id",
        ),
        (
            _done_payload(
                dod_results=[
                    {
                        "dod_id": "tests-pass",
                        "status": "passed",
                        "evidence": ["pytest"],
                        "summary": "only one",
                    }
                ]
            ),
            "missing_required_dod",
        ),
        (
            _done_payload(
                dod_results=[
                    {
                        "dod_id": "tests-pass",
                        "status": "failed",
                        "evidence": ["pytest failed"],
                        "summary": "not terminal success",
                    },
                    {
                        "dod_id": "docs-reviewed",
                        "status": "not_applicable",
                        "evidence": [],
                        "summary": "docs skipped",
                    },
                ]
            ),
            "incomplete_dod",
        ),
        (
            _done_payload(
                dod_results=[
                    {
                        "dod_id": "tests-pass",
                        "status": "passed",
                        "evidence": [],
                        "summary": "missing evidence",
                    },
                    {
                        "dod_id": "docs-reviewed",
                        "status": "not_applicable",
                        "evidence": [],
                        "summary": "docs skipped",
                    },
                ]
            ),
            "no_evidence",
        ),
    ],
)
async def test_submit_job_done_contract_violations_return_details_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    payload: dict[str, object],
    rule: str,
) -> None:
    _actor_id, key, job_id = _claimed_job(conn, title=f"submit {rule}")

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"] == "contract_violation"
    assert response.json()["details"]["rule"] == rule
    rows = audit_rows(conn)
    assert rows[0]["error_code"] == "contract_violation"
    assert rows[0]["response_payload"]["details"]["rule"] == rule
    assert job_row(conn, job_id)["state"] == "in_progress"
    assert _decision_rows(conn, job_id) == []
    assert _learning_rows(conn, job_id) == []


@pytest.mark.asyncio
async def test_submit_job_done_bad_status_is_pydantic_422_not_audited(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, job_id = _claimed_job(conn)
    payload = _done_payload(
        dod_results=[
            {
                "dod_id": "tests-pass",
                "status": "wat",
                "evidence": ["pytest"],
                "summary": "bad status",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "docs skipped",
            },
        ]
    )

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 422
    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_submit_job_done_mcp_returns_multipart_and_structured_payload(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key, job_id = _claimed_job(conn, title="mcp submit target")

    result = await _mcp_call(
        actor_id,
        "submit_job",
        {
            "job_id": str(job_id),
            "payload": _done_payload(
                decisions_made=[],
                learnings=[],
            ),
            "agent_identity": "submit-test-mcp",
        },
    )

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    response = SubmitJobResponse.model_validate(structured)
    assert response.job.id == job_id
    assert response.job.state == "done"
    assert response.created_decisions == []
    assert response.created_learnings == []

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    first_block = json.loads(content[0]["text"])
    assert first_block == {"job": structured["job"]}
    assert "Job is now done" in content[1]["text"]
