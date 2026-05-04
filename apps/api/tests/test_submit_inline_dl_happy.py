from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import psycopg
import pytest
from _submit_job_test_support import DB_SKIP, auth_headers, claimed_job
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


def _large_decisions() -> list[dict[str, object]]:
    return [
        {
            "title": f"Decision {index:02d}",
            "statement": f"Decision statement {index:02d}",
            "rationale": f"Decision rationale {index:02d}",
        }
        for index in range(10)
    ]


def _large_learnings() -> list[dict[str, object]]:
    return [
        {
            "title": f"Learning {index:02d}",
            "statement": f"Learning statement {index:02d}",
            "context": f"Learning context {index:02d}",
        }
        for index in range(10)
    ]


def _done_payload(
    *,
    decisions_made: list[dict[str, object]] | None = None,
    learnings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": ["pytest -q apps/api/tests/test_submit_inline_dl_happy.py"],
                "summary": "targeted D&L tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "no docs touched",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_submit_inline_dl_happy.py"],
        "verification_summary": "large inline D&L batch verified",
        "files_changed": ["apps/api/tests/test_submit_inline_dl_happy.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-80",
        "decisions_made": decisions_made or [],
        "learnings": learnings or [],
    }


def _decision_rows(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, statement,
                   rationale, supersedes_decision_id, created_by_actor_id,
                   created_at
            FROM decisions
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY title
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
                   context, created_by_actor_id, created_at
            FROM learnings
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY title
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _decision_rows_by_kind(
    conn: Connection[tuple[object, ...]],
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, created_by_actor_id
            FROM decisions
            ORDER BY title
            """
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _learning_rows_by_kind(
    conn: Connection[tuple[object, ...]],
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, created_by_actor_id
            FROM learnings
            ORDER BY title
            """
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_submit_inline_dl_large_batch_preserves_content_and_metadata(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)
    decisions = _large_decisions()
    learnings = _large_learnings()
    payload = _done_payload(decisions_made=decisions, learnings=learnings)

    before = datetime.now(UTC)
    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )
    after = datetime.now(UTC) + timedelta(seconds=5)

    assert response.status_code == 200
    submit_response = SubmitJobResponse.model_validate(response.json())
    assert len(submit_response.created_decisions) == 10
    assert len(submit_response.created_learnings) == 10

    stored_decisions = _decision_rows(conn, job_id)
    stored_learnings = _learning_rows(conn, job_id)
    assert {row["id"] for row in stored_decisions} == set(
        submit_response.created_decisions
    )
    assert {row["id"] for row in stored_learnings} == set(
        submit_response.created_learnings
    )
    assert len(stored_decisions) == 10
    assert len(stored_learnings) == 10

    decisions_by_title = {str(item["title"]): item for item in decisions}
    for row in stored_decisions:
        original = decisions_by_title[str(row["title"])]
        created_at = row["created_at"]
        assert isinstance(created_at, datetime)
        assert before <= created_at.astimezone(UTC) <= after
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["statement"] == original["statement"]
        assert row["rationale"] == original["rationale"]
        assert row["created_by_actor_id"] == actor_id
        assert row["supersedes_decision_id"] is None

    learnings_by_title = {str(item["title"]): item for item in learnings}
    for row in stored_learnings:
        original = learnings_by_title[str(row["title"])]
        created_at = row["created_at"]
        assert isinstance(created_at, datetime)
        assert before <= created_at.astimezone(UTC) <= after
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["statement"] == original["statement"]
        assert row["context"] == original["context"]
        assert row["created_by_actor_id"] == actor_id


@pytest.mark.asyncio
async def test_submit_inline_dl_empty_arrays_create_no_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(decisions_made=[], learnings=[]),
    )

    assert response.status_code == 200
    submit_response = SubmitJobResponse.model_validate(response.json())
    assert submit_response.created_decisions == []
    assert submit_response.created_learnings == []
    assert _decision_rows(conn, job_id) == []
    assert _learning_rows(conn, job_id) == []


@pytest.mark.asyncio
async def test_submit_inline_dl_can_attach_to_job_pipeline_and_project(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id, job_id = claimed_job(conn)
    decisions = [
        {
            "title": "Job decision",
            "statement": "Job-local decision stays attached to the submitting job.",
        },
        {
            "title": "Pipeline decision",
            "statement": "Pipeline decision attaches to the submitting job pipeline.",
            "attached_to_kind": "pipeline",
        },
        {
            "title": "Project decision",
            "statement": "Project decision attaches to the submitting job project.",
            "attached_to_kind": "project",
        },
    ]
    learnings = [
        {
            "title": "Job learning",
            "statement": "Job-local learning stays attached to the submitting job.",
        },
        {
            "title": "Pipeline learning",
            "statement": "Pipeline learning attaches to the submitting job pipeline.",
            "attached_to_kind": "pipeline",
        },
        {
            "title": "Project learning",
            "statement": "Project learning attaches to the submitting job project.",
            "attached_to_kind": "project",
        },
    ]

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(decisions_made=decisions, learnings=learnings),
    )

    assert response.status_code == 200
    submit_response = SubmitJobResponse.model_validate(response.json())
    assert len(submit_response.created_decisions) == 3
    assert len(submit_response.created_learnings) == 3

    decision_targets = {
        str(row["title"]): (row["attached_to_kind"], row["attached_to_id"])
        for row in _decision_rows_by_kind(conn)
    }
    learning_targets = {
        str(row["title"]): (row["attached_to_kind"], row["attached_to_id"])
        for row in _learning_rows_by_kind(conn)
    }
    assert decision_targets == {
        "Job decision": ("job", job_id),
        "Pipeline decision": ("pipeline", pipeline_id),
        "Project decision": ("project", project_id),
    }
    assert learning_targets == {
        "Job learning": ("job", job_id),
        "Pipeline learning": ("pipeline", pipeline_id),
        "Project learning": ("project", project_id),
    }
    for row in _decision_rows_by_kind(conn) + _learning_rows_by_kind(conn):
        assert row["created_by_actor_id"] == actor_id
