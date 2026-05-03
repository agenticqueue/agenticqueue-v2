from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import JobState, ReviewCompleteRequest, ReviewCompleteResponse
from aq_api.models.db import Job as DbJob
from aq_api.services.jobs import job_from_db

REVIEW_COMPLETE_OP = "review_complete"
JOB_TARGET_KIND = "job"
PENDING_REVIEW_STATE: JobState = "pending_review"


async def _job_for_update(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id).with_for_update()
    return cast(DbJob | None, await session.scalar(statement))


async def review_complete(
    session: AsyncSession,
    *,
    job_id: UUID,
    request: ReviewCompleteRequest,
    actor_id: UUID,
) -> ReviewCompleteResponse:
    _ = actor_id
    response: ReviewCompleteResponse | None = None
    request_payload = {
        "job_id": str(job_id),
        **request.model_dump(mode="json"),
    }

    async with audited_op(
        session,
        op=REVIEW_COMPLETE_OP,
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
        if state != PENDING_REVIEW_STATE:
            raise BusinessRuleException(
                status_code=409,
                error_code="job_not_pending_review",
                message="job is not pending review",
            )

        db_job.state = request.final_outcome
        await session.flush()
        response = ReviewCompleteResponse(job=job_from_db(db_job))
        audit.response_payload = {
            "final_outcome": request.final_outcome,
            "prior_state": PENDING_REVIEW_STATE,
        }

    assert response is not None
    return response
