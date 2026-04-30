from typing import cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    JobState,
    SubmitDecisionInline,
    SubmitJobDoneRequest,
    SubmitJobRequest,
    SubmitJobResponse,
    SubmitLearningInline,
)
from aq_api.models.db import Decision as DbDecision
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Learning as DbLearning
from aq_api.services._contract_validator import validate_done_submission
from aq_api.services.jobs import job_from_db

SUBMIT_JOB_OP = "submit_job"
JOB_TARGET_KIND = "job"
IN_PROGRESS_STATE: JobState = "in_progress"
DONE_STATE: JobState = "done"


async def _job_for_update(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id).with_for_update()
    return cast(DbJob | None, await session.scalar(statement))


async def _insert_inline_dl(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
    decisions_made: list[SubmitDecisionInline],
    learnings: list[SubmitLearningInline],
) -> tuple[list[UUID], list[UUID]]:
    created_decisions: list[UUID] = []
    for decision in decisions_made:
        result = await session.execute(
            insert(DbDecision)
            .values(
                attached_to_kind="job",
                attached_to_id=job_id,
                title=decision.title,
                statement=decision.statement,
                rationale=decision.rationale,
                supersedes_decision_id=None,
                created_by_actor_id=actor_id,
            )
            .returning(DbDecision.id)
        )
        created_decisions.append(result.scalar_one())

    created_learnings: list[UUID] = []
    for learning in learnings:
        result = await session.execute(
            insert(DbLearning)
            .values(
                attached_to_kind="job",
                attached_to_id=job_id,
                title=learning.title,
                statement=learning.statement,
                context=learning.context,
                created_by_actor_id=actor_id,
            )
            .returning(DbLearning.id)
        )
        created_learnings.append(result.scalar_one())

    return created_decisions, created_learnings


def _clear_claim(db_job: DbJob) -> None:
    db_job.claimed_by_actor_id = None
    db_job.claimed_at = None
    db_job.claim_heartbeat_at = None


async def submit_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    request: SubmitJobRequest,
    actor_id: UUID,
) -> SubmitJobResponse:
    if not isinstance(request, SubmitJobDoneRequest):
        raise BusinessRuleException(
            status_code=422,
            error_code="unsupported_submit_outcome",
            message="only outcome='done' is implemented in Story 5.2",
        )

    response: SubmitJobResponse | None = None
    request_payload = {
        "job_id": str(job_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=SUBMIT_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=request_payload,
    ) as audit:
        db_job = await _job_for_update(session, job_id)
        if db_job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        state = cast(JobState, db_job.state)
        if state != IN_PROGRESS_STATE:
            raise BusinessRuleException(
                status_code=409,
                error_code="job_not_in_progress",
                message="job is not in progress",
            )

        if db_job.claimed_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="submit_forbidden",
                message="job is claimed by a different actor",
            )

        validate_done_submission(db_job.contract, request)

        db_job.state = DONE_STATE
        _clear_claim(db_job)
        created_decisions, created_learnings = await _insert_inline_dl(
            session,
            job_id=job_id,
            actor_id=actor_id,
            decisions_made=request.decisions_made,
            learnings=request.learnings,
        )
        await session.flush()

        response = SubmitJobResponse(
            job=job_from_db(db_job),
            created_decisions=created_decisions,
            created_learnings=created_learnings,
            created_gated_on_edge=False,
        )
        audit.response_payload = {
            "outcome": request.outcome,
            "created_decisions": [str(value) for value in created_decisions],
            "created_learnings": [str(value) for value in created_learnings],
            "created_gated_on_edge": False,
        }

    assert response is not None
    return response
