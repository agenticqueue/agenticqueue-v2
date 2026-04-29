from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    JobState,
    ReleaseJobResponse,
    ResetClaimRequest,
    ResetClaimResponse,
)
from aq_api.models.db import Job as DbJob
from aq_api.services.jobs import job_from_db

RELEASE_JOB_OP = "release_job"
RESET_CLAIM_OP = "reset_claim"
JOB_TARGET_KIND = "job"
READY_STATE: JobState = "ready"
IN_PROGRESS_STATE: JobState = "in_progress"


async def _job_for_update(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id).with_for_update()
    return cast(DbJob | None, await session.scalar(statement))


def _assert_claimed(db_job: DbJob) -> None:
    state = cast(JobState, db_job.state)
    if state != IN_PROGRESS_STATE or db_job.claimed_by_actor_id is None:
        raise BusinessRuleException(
            status_code=409,
            error_code="job_not_claimed",
            message="job is not currently claimed",
        )


def _clear_claim(db_job: DbJob) -> None:
    db_job.state = READY_STATE
    db_job.claimed_by_actor_id = None
    db_job.claimed_at = None
    db_job.claim_heartbeat_at = None


async def release_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
) -> ReleaseJobResponse:
    response: ReleaseJobResponse | None = None
    request_payload = {"job_id": str(job_id)}
    async with audited_op(
        session,
        op=RELEASE_JOB_OP,
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

        _assert_claimed(db_job)
        if db_job.claimed_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="release_forbidden",
                message="job is claimed by a different actor",
            )

        _clear_claim(db_job)
        await session.flush()
        response = ReleaseJobResponse(job=job_from_db(db_job))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def reset_claim(
    session: AsyncSession,
    *,
    job_id: UUID,
    request: ResetClaimRequest,
    actor_id: UUID,
) -> ResetClaimResponse:
    response: ResetClaimResponse | None = None
    request_payload = {
        "job_id": str(job_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=RESET_CLAIM_OP,
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

        _assert_claimed(db_job)
        _clear_claim(db_job)
        await session.flush()
        response = ResetClaimResponse(job=job_from_db(db_job))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
