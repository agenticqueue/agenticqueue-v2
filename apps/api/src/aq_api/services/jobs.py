import base64
import copy
import json
from datetime import datetime
from typing import cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    CreateJobRequest,
    CreateJobResponse,
    GetJobResponse,
    Job,
    JobState,
    ListJobsResponse,
    UpdateJobRequest,
    UpdateJobResponse,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
CREATE_JOB_OP = "create_job"
UPDATE_JOB_OP = "update_job"
JOB_TARGET_KIND = "job"
READY_STATE: JobState = "ready"
FORBIDDEN_UPDATE_FIELDS = {
    "state": "cannot_write_state_via_update",
    "labels": "cannot_write_labels_via_update",
    "contract": "cannot_write_contract_via_update",
}
CLAIM_UPDATE_FIELDS = {
    "claimed_by_actor_id",
    "claimed_at",
    "claim_heartbeat_at",
}


class InvalidJobCursorError(Exception):
    pass


class JobNotFoundError(Exception):
    pass


def job_from_db(job: DbJob) -> Job:
    return Job(
        id=job.id,
        pipeline_id=job.pipeline_id,
        project_id=job.project_id,
        state=cast(JobState, job.state),
        title=job.title,
        description=job.description,
        contract=job.contract,
        labels=list(job.labels or []),
        claimed_by_actor_id=job.claimed_by_actor_id,
        claimed_at=job.claimed_at,
        claim_heartbeat_at=job.claim_heartbeat_at,
        created_at=job.created_at,
        created_by_actor_id=job.created_by_actor_id,
    )


def encode_job_cursor(job: DbJob) -> str:
    payload = json.dumps(
        {
            "created_at": job.created_at.isoformat(),
            "id": str(job.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_job_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        job_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidJobCursorError("invalid job cursor") from exc
    return created_at, job_id


async def create_job(
    session: AsyncSession,
    request: CreateJobRequest,
    *,
    actor_id: UUID,
) -> CreateJobResponse:
    response: CreateJobResponse | None = None
    async with audited_op(
        session,
        op=CREATE_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        request_payload=request.model_dump(mode="json", exclude_none=True),
    ) as audit:
        pipeline = await session.get(DbPipeline, request.pipeline_id)
        if pipeline is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="pipeline_not_found",
                message="pipeline not found",
            )

        db_job = DbJob(
            pipeline_id=pipeline.id,
            project_id=pipeline.project_id,
            state=READY_STATE,
            title=request.title,
            description=request.description,
            contract=copy.deepcopy(request.contract),
            created_by_actor_id=actor_id,
        )
        session.add(db_job)
        await session.flush()

        response = CreateJobResponse(job=job_from_db(db_job))
        audit.target_id = db_job.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def list_jobs(
    session: AsyncSession,
    *,
    project_id: UUID | None = None,
    pipeline_id: UUID | None = None,
    state: JobState | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
) -> ListJobsResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbJob)
    if project_id is not None:
        statement = statement.where(DbJob.project_id == project_id)
    if pipeline_id is not None:
        statement = statement.where(DbJob.pipeline_id == pipeline_id)
    if state is not None:
        statement = statement.where(DbJob.state == state)

    if cursor is not None:
        created_at, job_id = decode_job_cursor(cursor)
        statement = statement.where(
            or_(
                DbJob.created_at > created_at,
                and_(
                    DbJob.created_at == created_at,
                    DbJob.id > job_id,
                ),
            )
        )

    statement = statement.order_by(DbJob.created_at.asc(), DbJob.id.asc()).limit(
        bounded_limit + 1
    )
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_job_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListJobsResponse(
        jobs=[job_from_db(job) for job in page_rows],
        next_cursor=next_cursor,
    )


async def get_job(session: AsyncSession, job_id: UUID) -> GetJobResponse:
    db_job = await session.get(DbJob, job_id)
    if db_job is None:
        raise JobNotFoundError("job not found")
    return GetJobResponse(job=job_from_db(db_job))


async def update_job(
    session: AsyncSession,
    job_id: UUID,
    request_payload: dict[str, object],
) -> UpdateJobResponse:
    response: UpdateJobResponse | None = None
    full_request_payload = {"job_id": str(job_id), **request_payload}
    async with audited_op(
        session,
        op=UPDATE_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=full_request_payload,
    ) as audit:
        db_job = await session.get(DbJob, job_id)
        if db_job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        for field, error_code in FORBIDDEN_UPDATE_FIELDS.items():
            if field in request_payload:
                raise BusinessRuleException(
                    status_code=400,
                    error_code=error_code,
                    message=f"{field} cannot be written via update_job",
                )
        if any(field in request_payload for field in CLAIM_UPDATE_FIELDS):
            raise BusinessRuleException(
                status_code=400,
                error_code="cannot_write_claim_via_update",
                message="claim metadata cannot be written via update_job",
            )

        try:
            request = UpdateJobRequest.model_validate(request_payload)
        except ValidationError as exc:
            raise BusinessRuleException(
                status_code=422,
                error_code="invalid_job_update",
                message="invalid job update payload",
            ) from exc

        if "title" in request.model_fields_set and request.title is not None:
            db_job.title = request.title
        if "description" in request.model_fields_set:
            db_job.description = request.description

        await session.flush()
        response = UpdateJobResponse(job=job_from_db(db_job))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
