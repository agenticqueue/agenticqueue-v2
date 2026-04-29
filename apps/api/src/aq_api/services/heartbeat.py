from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import HeartbeatJobResponse, JobState
from aq_api.models.db import Job as DbJob
from aq_api.services.jobs import job_from_db

HEARTBEAT_JOB_OP = "heartbeat_job"
JOB_TARGET_KIND = "job"
IN_PROGRESS_STATE: JobState = "in_progress"


async def _job_for_update(session: AsyncSession, job_id: UUID) -> DbJob | None:
    statement = select(DbJob).where(DbJob.id == job_id).with_for_update()
    return cast(DbJob | None, await session.scalar(statement))


async def heartbeat_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
) -> HeartbeatJobResponse:
    response: HeartbeatJobResponse | None = None
    request_payload = {"job_id": str(job_id)}
    async with audited_op(
        session,
        op=HEARTBEAT_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=request_payload,
        skip_success_audit=True,
    ) as audit:
        db_job = await _job_for_update(session, job_id)
        if db_job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        state = cast(JobState, db_job.state)
        if state != IN_PROGRESS_STATE or db_job.claimed_by_actor_id is None:
            raise BusinessRuleException(
                status_code=409,
                error_code="job_not_in_progress",
                message="job is not in progress",
            )

        if db_job.claimed_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="heartbeat_forbidden",
                message="job is claimed by a different actor",
            )

        db_job.claim_heartbeat_at = datetime.now(UTC)
        await session.flush()
        response = HeartbeatJobResponse(job=job_from_db(db_job))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
