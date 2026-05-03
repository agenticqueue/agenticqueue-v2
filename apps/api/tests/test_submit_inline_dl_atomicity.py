from uuid import UUID

import pytest
from _submit_job_test_support import (
    CONTRACT,
    DB_SKIP,
    audit_rows,
    blocked_payload,
    claimed_job,
    decision_rows,
    gated_edge_count,
    insert_job,
    job_row,
    learning_rows,
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
from aq_api.models import SubmitJobBlockedRequest, SubmitJobDoneRequest
from aq_api.models.db import Decision as DbDecision
from psycopg import Connection
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = DB_SKIP


def _done_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": [
                    "pytest -q apps/api/tests/test_submit_inline_dl_atomicity.py"
                ],
                "summary": "atomicity tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "no docs touched",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_submit_inline_dl_atomicity.py"],
        "verification_summary": "atomic submit rollback verified",
        "files_changed": ["apps/api/tests/test_submit_inline_dl_atomicity.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-80",
        "decisions_made": [
            {
                "title": f"Decision {index}",
                "statement": f"Decision statement {index}",
                "rationale": f"Decision rationale {index}",
            }
            for index in range(3)
        ],
        "learnings": [
            {
                "title": f"Learning {index}",
                "statement": f"Learning statement {index}",
                "context": f"Learning context {index}",
            }
            for index in range(2)
        ],
    }
    payload.update(overrides)
    return payload


def _assert_no_partial_state(
    conn: Connection[tuple[object, ...]],
    *,
    job_id: UUID,
    actor_id: UUID,
    gated_job_id: UUID | None = None,
) -> None:
    row = job_row(conn, job_id)
    assert row["state"] == "in_progress"
    assert row["claimed_by_actor_id"] == actor_id
    assert row["claimed_at"] is not None
    assert row["claim_heartbeat_at"] is not None
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    if gated_job_id is not None:
        assert gated_edge_count(conn, from_job_id=job_id, to_job_id=gated_job_id) == 0
    assert audit_rows(conn) == []


async def _submit_done(job_id: UUID, actor_id: UUID) -> None:
    import aq_api.services.submit as submit_service
    from aq_api._db import SessionLocal

    async with SessionLocal() as session:
        await submit_service.submit_job(
            session,
            job_id=job_id,
            request=SubmitJobDoneRequest.model_validate(_done_payload()),
            actor_id=actor_id,
        )


async def _submit_blocked(job_id: UUID, actor_id: UUID, gated_job_id: UUID) -> None:
    import aq_api.services.submit as submit_service
    from aq_api._db import SessionLocal

    async with SessionLocal() as session:
        await submit_service.submit_job(
            session,
            job_id=job_id,
            request=SubmitJobBlockedRequest.model_validate(
                blocked_payload(gated_job_id)
            ),
            actor_id=actor_id,
        )


@pytest.mark.asyncio
async def test_atomicity_failure_after_state_update_before_inline_dl_rolls_back(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service

    actor_id, _key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    async def fail_before_dl(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[list[UUID], list[UUID]]:
        raise RuntimeError("synthetic before dl")

    monkeypatch.setattr(submit_service, "_insert_inline_dl", fail_before_dl)

    with pytest.raises(RuntimeError, match="synthetic before dl"):
        await _submit_done(job_id, actor_id)

    _assert_no_partial_state(conn, job_id=job_id, actor_id=actor_id)


@pytest.mark.asyncio
async def test_atomicity_failure_after_first_decision_insert_rolls_back_all_dl(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service

    actor_id, _key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    async def insert_one_decision_then_fail(
        session: AsyncSession,
        *,
        job_id: UUID,
        actor_id: UUID,
        decisions_made: object,
        learnings: object,
    ) -> tuple[list[UUID], list[UUID]]:
        del decisions_made, learnings
        await session.execute(
            insert(DbDecision).values(
                attached_to_kind="job",
                attached_to_id=job_id,
                title="inserted before failure",
                statement="this row must roll back",
                rationale=None,
                supersedes_decision_id=None,
                created_by_actor_id=actor_id,
            )
        )
        raise RuntimeError("synthetic after first decision")

    monkeypatch.setattr(
        submit_service,
        "_insert_inline_dl",
        insert_one_decision_then_fail,
    )

    with pytest.raises(RuntimeError, match="synthetic after first decision"):
        await _submit_done(job_id, actor_id)

    _assert_no_partial_state(conn, job_id=job_id, actor_id=actor_id)


@pytest.mark.asyncio
async def test_atomicity_failure_after_dl_before_audit_rolls_back_everything(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service

    actor_id, _key, _project_id, _pipeline_id, job_id = claimed_job(conn)
    original_insert_inline_dl = submit_service._insert_inline_dl

    async def insert_all_dl_then_fail(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[UUID], list[UUID]]:
        await original_insert_inline_dl(*args, **kwargs)
        raise RuntimeError("synthetic after dl before audit")

    monkeypatch.setattr(submit_service, "_insert_inline_dl", insert_all_dl_then_fail)

    with pytest.raises(RuntimeError, match="synthetic after dl before audit"):
        await _submit_done(job_id, actor_id)

    _assert_no_partial_state(conn, job_id=job_id, actor_id=actor_id)


@pytest.mark.asyncio
async def test_atomicity_blocked_failure_after_edge_before_dl_rolls_back_edge(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service

    actor_id, _key, project_id, pipeline_id, job_id = claimed_job(conn)
    gated_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="gating job",
        contract=CONTRACT,
    )

    async def fail_before_dl(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[list[UUID], list[UUID]]:
        raise RuntimeError("synthetic blocked before dl")

    monkeypatch.setattr(submit_service, "_insert_inline_dl", fail_before_dl)

    with pytest.raises(RuntimeError, match="synthetic blocked before dl"):
        await _submit_blocked(job_id, actor_id, gated_job_id)

    _assert_no_partial_state(
        conn,
        job_id=job_id,
        actor_id=actor_id,
        gated_job_id=gated_job_id,
    )


@pytest.mark.asyncio
async def test_atomicity_blocked_failure_after_dl_before_audit_rolls_back_edge_and_dl(
    conn: Connection[tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aq_api.services.submit as submit_service

    actor_id, _key, project_id, pipeline_id, job_id = claimed_job(conn)
    gated_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="gating job",
        contract=CONTRACT,
    )
    original_insert_inline_dl = submit_service._insert_inline_dl

    async def insert_all_dl_then_fail(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[UUID], list[UUID]]:
        await original_insert_inline_dl(*args, **kwargs)
        raise RuntimeError("synthetic blocked after dl before audit")

    monkeypatch.setattr(submit_service, "_insert_inline_dl", insert_all_dl_then_fail)

    with pytest.raises(RuntimeError, match="synthetic blocked after dl before audit"):
        await _submit_blocked(job_id, actor_id, gated_job_id)

    _assert_no_partial_state(
        conn,
        job_id=job_id,
        actor_id=actor_id,
        gated_job_id=gated_job_id,
    )
