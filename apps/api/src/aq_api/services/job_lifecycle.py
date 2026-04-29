from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import CancelJobResponse, JobState
from aq_api.models.db import Job as DbJob
from aq_api.services.jobs import job_from_db

CANCEL_JOB_OP = "cancel_job"
JOB_TARGET_KIND = "job"
CANCELLED_STATE: JobState = "cancelled"
TERMINAL_STATES: frozenset[JobState] = frozenset({"done", "failed", "cancelled"})


async def cancel_job(session: AsyncSession, job_id: UUID) -> CancelJobResponse:
    response: CancelJobResponse | None = None
    async with audited_op(
        session,
        op=CANCEL_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload={"job_id": str(job_id)},
    ) as audit:
        db_job = await session.get(DbJob, job_id)
        if db_job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        state = cast(JobState, db_job.state)
        if state in TERMINAL_STATES:
            audit.response_payload = {"error": "already_terminal", "state": state}
            raise BusinessRuleException(
                status_code=409,
                error_code="already_terminal",
                message="job is already terminal",
            )

        db_job.state = CANCELLED_STATE
        await session.flush()
        response = CancelJobResponse(job=job_from_db(db_job))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
