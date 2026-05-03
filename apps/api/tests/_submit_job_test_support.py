from __future__ import annotations

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
from aq_api.app import app
from aq_api.models import SubmitJobResponse
from psycopg import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_SKIP = pytest.mark.skipif(
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


def fixture_project(
    conn: Connection[tuple[object, ...]],
    *,
    actor_name: str | None = None,
) -> tuple[UUID, str, UUID, UUID]:
    actor_id, key = insert_actor_with_key(conn, name=actor_name)
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    return actor_id, key, project_id, pipeline_id


def mark_claimed(
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


def claimed_job(
    conn: Connection[tuple[object, ...]],
    *,
    contract: dict[str, object] | None = None,
    title: str = "submit target",
) -> tuple[UUID, str, UUID, UUID, UUID]:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title=title,
        contract=contract or CONTRACT,
    )
    mark_claimed(conn, job_id, actor_id=actor_id)
    return actor_id, key, project_id, pipeline_id, job_id


def inline_decisions() -> list[dict[str, object]]:
    return [
        {
            "title": "Capture outcome",
            "statement": "Submit records the outcome and durable decision.",
            "rationale": "Later caps surface these nodes.",
        },
        {
            "title": "Clear claim fields",
            "statement": "All submit outcomes clear claim ownership.",
            "rationale": "Submitted Jobs are no longer actively leased.",
        },
    ]


def inline_learnings() -> list[dict[str, object]]:
    return [
        {
            "title": "Outcome routing",
            "statement": "Each submit outcome maps to a distinct Job state.",
            "context": "Story 5.3 extends the Story 5.2 done path.",
        },
        {
            "title": "Inline attachment",
            "statement": "Inline D&L rows stay attached to the submitting Job.",
            "context": "Cap #9 will surface them later.",
        },
    ]


def pending_review_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "pending_review",
        "submitted_for_review": "needs reviewer eyes",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "failed",
                "evidence": [],
                "summary": "tests need reviewer judgment",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "blocked",
                "evidence": [],
                "summary": "docs wait on review",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_submit_job_pending_review.py"],
        "verification_summary": "pending review submit path exercised",
        "files_changed": ["apps/api/src/aq_api/services/submit.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-78",
        "decisions_made": inline_decisions(),
        "learnings": inline_learnings(),
    }
    payload.update(overrides)
    return payload


def failed_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "failed",
        "failure_reason": "implementation could not satisfy the contract",
        "files_changed": ["apps/api/src/aq_api/services/submit.py"],
        "risks_or_deviations": ["follow-up required"],
        "handoff": "AQ2-78",
        "decisions_made": inline_decisions(),
        "learnings": inline_learnings(),
    }
    payload.update(overrides)
    return payload


def blocked_payload(gated_on_job_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "blocked",
        "gated_on_job_id": str(gated_on_job_id),
        "blocker_reason": "waiting on the gating Job",
        "files_changed": ["apps/api/src/aq_api/services/submit.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-78",
        "decisions_made": inline_decisions(),
        "learnings": inline_learnings(),
    }
    payload.update(overrides)
    return payload


def decision_rows(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title,
                   supersedes_decision_id, created_by_actor_id
            FROM decisions
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY created_at, id
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def learning_rows(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, created_by_actor_id
            FROM learnings
            WHERE attached_to_kind = 'job' AND attached_to_id = %s
            ORDER BY created_at, id
            """,
            (job_id,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def gated_edge_count(
    conn: Connection[tuple[object, ...]],
    *,
    from_job_id: UUID,
    to_job_id: UUID,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM job_edges
            WHERE from_job_id = %s AND to_job_id = %s AND edge_type = 'gated_on'
            """,
            (from_job_id, to_job_id),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def insert_gated_edge(
    conn: Connection[tuple[object, ...]],
    *,
    from_job_id: UUID,
    to_job_id: UUID,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO job_edges (from_job_id, to_job_id, edge_type)
            VALUES (%s, %s, 'gated_on')
            """,
            (from_job_id, to_job_id),
        )


def assert_claim_cleared(row: dict[str, object]) -> None:
    assert row["claimed_by_actor_id"] is None
    assert row["claimed_at"] is None
    assert row["claim_heartbeat_at"] is None


def assert_inline_dl_created(
    conn: Connection[tuple[object, ...]],
    *,
    job_id: UUID,
    actor_id: UUID,
    response: SubmitJobResponse,
) -> None:
    decisions = decision_rows(conn, job_id)
    learnings = learning_rows(conn, job_id)
    assert {row["id"] for row in decisions} == set(response.created_decisions)
    assert {row["id"] for row in learnings} == set(response.created_learnings)
    assert len(decisions) == 2
    assert len(learnings) == 2
    for row in decisions:
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["created_by_actor_id"] == actor_id
        assert row["supersedes_decision_id"] is None
    for row in learnings:
        assert row["attached_to_kind"] == "job"
        assert row["attached_to_id"] == job_id
        assert row["created_by_actor_id"] == actor_id


def unknown_job_id() -> UUID:
    return uuid4()


__all__ = [
    "CONTRACT",
    "DB_SKIP",
    "assert_claim_cleared",
    "assert_inline_dl_created",
    "audit_rows",
    "auth_headers",
    "blocked_payload",
    "claimed_job",
    "decision_rows",
    "failed_payload",
    "fixture_project",
    "gated_edge_count",
    "insert_actor_with_key",
    "insert_gated_edge",
    "insert_job",
    "insert_pipeline",
    "insert_project",
    "job_row",
    "learning_rows",
    "mark_claimed",
    "pending_review_payload",
    "unknown_job_id",
]
