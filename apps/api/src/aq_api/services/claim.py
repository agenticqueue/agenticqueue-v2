from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    ClaimNextJobRequest,
    ClaimNextJobResponse,
    ContextPacketStub,
    JobState,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.services.jobs import job_from_db

CLAIM_NEXT_JOB_OP = "claim_next_job"
JOB_TARGET_KIND = "job"
READY_STATE: JobState = "ready"
IN_PROGRESS_STATE: JobState = "in_progress"
RECOMMENDED_HEARTBEAT_AFTER_SECONDS = 30


async def claim_next_job(
    session: AsyncSession,
    *,
    request: ClaimNextJobRequest,
    actor_id: UUID,
) -> ClaimNextJobResponse:
    labels = list(request.label_filter or [])
    request_payload: dict[str, object] = {
        "project_id": str(request.project_id),
        "label_filter": labels,
    }
    response: ClaimNextJobResponse | None = None

    async with audited_op(
        session,
        op=CLAIM_NEXT_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        request_payload=request_payload,
    ) as audit:
        statement = (
            select(DbJob)
            .join(DbPipeline, DbJob.pipeline_id == DbPipeline.id)
            .where(
                DbJob.state == READY_STATE,
                DbJob.project_id == request.project_id,
                DbPipeline.is_template.is_(False),
                DbPipeline.archived_at.is_(None),
            )
            .order_by(DbJob.created_at.asc(), DbJob.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True, of=DbJob)
        )
        if labels:
            statement = statement.where(DbJob.labels.contains(labels))

        db_job = await session.scalar(statement)
        if db_job is None:
            raise BusinessRuleException(
                status_code=409,
                error_code="no_ready_job",
                message="no ready job matched the claim request",
            )

        now = datetime.now(UTC)
        db_job.state = IN_PROGRESS_STATE
        db_job.claimed_by_actor_id = actor_id
        db_job.claimed_at = now
        db_job.claim_heartbeat_at = now
        await session.flush()

        from aq_api._settings import settings

        lease_seconds = settings.claim_lease_seconds
        response = ClaimNextJobResponse(
            job=job_from_db(db_job),
            packet=ContextPacketStub(
                project_id=db_job.project_id,
                pipeline_id=db_job.pipeline_id,
                current_job_id=db_job.id,
            ),
            lease_seconds=lease_seconds,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            recommended_heartbeat_after_seconds=RECOMMENDED_HEARTBEAT_AFTER_SECONDS,
        )
        audit.target_id = db_job.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
