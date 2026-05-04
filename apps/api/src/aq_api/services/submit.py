from typing import cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    JobState,
    SubmitDecisionInline,
    SubmitJobBlockedRequest,
    SubmitJobDoneRequest,
    SubmitJobFailedRequest,
    SubmitJobPendingReviewRequest,
    SubmitJobRequest,
    SubmitJobResponse,
    SubmitLearningInline,
)
from aq_api.models.db import Decision as DbDecision
from aq_api.models.db import Job as DbJob
from aq_api.models.db import JobEdge as DbJobEdge
from aq_api.models.db import Learning as DbLearning
from aq_api.services._contract_validator import (
    validate_done_submission,
    validate_failed_submission,
    validate_pending_review_submission,
)
from aq_api.services.jobs import job_from_db

SUBMIT_JOB_OP = "submit_job"
JOB_TARGET_KIND = "job"
IN_PROGRESS_STATE: JobState = "in_progress"
DONE_STATE: JobState = "done"
FAILED_STATE: JobState = "failed"
BLOCKED_STATE: JobState = "blocked"
PENDING_REVIEW_STATE: JobState = "pending_review"
GATED_ON_EDGE_TYPE = "gated_on"


async def _job_for_update(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id).with_for_update()
    return cast(DbJob | None, await session.scalar(statement))


async def _job_by_id(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id)
    return cast(DbJob | None, await session.scalar(statement))


async def _insert_inline_dl(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
    decisions_made: list[SubmitDecisionInline],
    learnings: list[SubmitLearningInline],
) -> tuple[list[UUID], list[UUID]]:
    job = await _job_by_id(session, job_id)
    assert job is not None

    created_decisions: list[UUID] = []
    for decision in decisions_made:
        attached_to_id = _inline_attached_to_id(job, decision.attached_to_kind)
        result = await session.execute(
            insert(DbDecision)
            .values(
                attached_to_kind=decision.attached_to_kind,
                attached_to_id=attached_to_id,
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
        attached_to_id = _inline_attached_to_id(job, learning.attached_to_kind)
        result = await session.execute(
            insert(DbLearning)
            .values(
                attached_to_kind=learning.attached_to_kind,
                attached_to_id=attached_to_id,
                title=learning.title,
                statement=learning.statement,
                context=learning.context,
                created_by_actor_id=actor_id,
            )
            .returning(DbLearning.id)
        )
        created_learnings.append(result.scalar_one())

    return created_decisions, created_learnings


def _inline_attached_to_id(job: DbJob, attached_to_kind: str) -> UUID:
    match attached_to_kind:
        case "job":
            return job.id
        case "pipeline":
            return job.pipeline_id
        case "project":
            return job.project_id
        case _:
            raise AssertionError(
                f"unknown inline attached_to_kind: {attached_to_kind!r}"
            )


async def _insert_gated_on_edge(
    session: AsyncSession,
    *,
    from_job_id: UUID,
    to_job_id: UUID,
) -> None:
    await session.execute(
        insert(DbJobEdge).values(
            from_job_id=from_job_id,
            to_job_id=to_job_id,
            edge_type=GATED_ON_EDGE_TYPE,
        )
    )


def _clear_claim(db_job: DbJob) -> None:
    db_job.claimed_by_actor_id = None
    db_job.claimed_at = None
    db_job.claim_heartbeat_at = None


async def _validate_gated_on_job(
    session: AsyncSession,
    *,
    submitting_job: DbJob,
    gated_on_job_id: UUID,
) -> None:
    if gated_on_job_id == submitting_job.id:
        raise BusinessRuleException(
            status_code=409,
            error_code="gated_on_invalid",
            message="a blocked Job cannot be gated on itself",
            details={"rule": "self"},
        )

    gated_job = await _job_by_id(session, gated_on_job_id)
    if gated_job is None:
        raise BusinessRuleException(
            status_code=409,
            error_code="gated_on_invalid",
            message="gated_on_job_id does not reference an existing Job",
            details={"rule": "not_found"},
        )

    if gated_job.project_id != submitting_job.project_id:
        raise BusinessRuleException(
            status_code=409,
            error_code="gated_on_invalid",
            message="gated_on_job_id must be in the same Project",
            details={"rule": "cross_project"},
        )


async def submit_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    request: SubmitJobRequest,
    actor_id: UUID,
) -> SubmitJobResponse:
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

        created_gated_on_edge = False
        if isinstance(request, SubmitJobDoneRequest):
            validate_done_submission(db_job.contract, request)
            next_state = DONE_STATE
        elif isinstance(request, SubmitJobPendingReviewRequest):
            validate_pending_review_submission(db_job.contract, request)
            next_state = PENDING_REVIEW_STATE
        elif isinstance(request, SubmitJobFailedRequest):
            validate_failed_submission(db_job.contract, request)
            next_state = FAILED_STATE
        elif isinstance(request, SubmitJobBlockedRequest):
            await _validate_gated_on_job(
                session,
                submitting_job=db_job,
                gated_on_job_id=request.gated_on_job_id,
            )
            next_state = BLOCKED_STATE
            created_gated_on_edge = True
        else:
            raise AssertionError(f"unknown submit outcome: {type(request)!r}")

        db_job.state = next_state
        _clear_claim(db_job)
        if isinstance(request, SubmitJobBlockedRequest):
            try:
                await _insert_gated_on_edge(
                    session,
                    from_job_id=job_id,
                    to_job_id=request.gated_on_job_id,
                )
            except IntegrityError as exc:
                await session.rollback()
                raise BusinessRuleException(
                    status_code=409,
                    error_code="gated_on_already_exists",
                    message="gated_on edge already exists",
                    details={"gated_on_job_id": str(request.gated_on_job_id)},
                ) from exc

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
            created_gated_on_edge=created_gated_on_edge,
        )
        audit.response_payload = {
            "outcome": request.outcome,
            "created_decisions": [str(value) for value in created_decisions],
            "created_learnings": [str(value) for value in created_learnings],
            "created_gated_on_edge": created_gated_on_edge,
        }

    assert response is not None
    return response
