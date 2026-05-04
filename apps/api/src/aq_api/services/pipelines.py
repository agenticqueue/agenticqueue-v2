import base64
import copy
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    ArchivePipelineResponse,
    ClonePipelineRequest,
    ClonePipelineResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    GetPipelineResponse,
    ListPipelinesResponse,
    Pipeline,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject
from aq_api.models.jobs import JobState
from aq_api.services._inheritance import (
    _resolve_attached_chain,
    decision_learning_inheritance_lists,
    decision_learning_scopes_for_entity,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CREATE_PIPELINE_OP = "create_pipeline"
UPDATE_PIPELINE_OP = "update_pipeline"
CLONE_PIPELINE_OP = "clone_pipeline"
ARCHIVE_PIPELINE_OP = "archive_pipeline"
PIPELINE_TARGET_KIND = "pipeline"
READY_STATE: JobState = "ready"

if TYPE_CHECKING:
    from aq_api.models import Job


class InvalidPipelineCursorError(Exception):
    pass


class PipelineNotFoundError(Exception):
    pass


def pipeline_from_db(pipeline: DbPipeline) -> Pipeline:
    return Pipeline(
        id=pipeline.id,
        project_id=pipeline.project_id,
        name=pipeline.name,
        is_template=pipeline.is_template,
        cloned_from_pipeline_id=pipeline.cloned_from_pipeline_id,
        archived_at=pipeline.archived_at,
        created_at=pipeline.created_at,
        created_by_actor_id=pipeline.created_by_actor_id,
    )


def job_from_db(job: DbJob) -> "Job":
    from aq_api.models import Job

    return Job(
        id=job.id,
        pipeline_id=job.pipeline_id,
        project_id=job.project_id,
        state=cast(JobState, job.state),
        title=job.title,
        description=job.description,
        contract=job.contract,
        labels=job.labels,
        claimed_by_actor_id=job.claimed_by_actor_id,
        claimed_at=job.claimed_at,
        claim_heartbeat_at=job.claim_heartbeat_at,
        created_at=job.created_at,
        created_by_actor_id=job.created_by_actor_id,
    )


def encode_pipeline_cursor(pipeline: DbPipeline) -> str:
    payload = json.dumps(
        {
            "created_at": pipeline.created_at.isoformat(),
            "id": str(pipeline.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_pipeline_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        pipeline_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidPipelineCursorError("invalid pipeline cursor") from exc
    return created_at, pipeline_id


async def list_pipelines(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
) -> ListPipelinesResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbPipeline).where(
        DbPipeline.is_template.is_(False),
        DbPipeline.archived_at.is_(None),
    )

    if cursor is not None:
        created_at, pipeline_id = decode_pipeline_cursor(cursor)
        statement = statement.where(
            or_(
                DbPipeline.created_at > created_at,
                and_(
                    DbPipeline.created_at == created_at,
                    DbPipeline.id > pipeline_id,
                ),
            )
        )

    statement = statement.order_by(
        DbPipeline.created_at.asc(),
        DbPipeline.id.asc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_pipeline_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListPipelinesResponse(
        pipelines=[pipeline_from_db(pipeline) for pipeline in page_rows],
        next_cursor=next_cursor,
    )


async def get_pipeline(
    session: AsyncSession,
    pipeline_id: UUID,
) -> GetPipelineResponse:
    pipeline = await session.get(DbPipeline, pipeline_id)
    if pipeline is None:
        raise PipelineNotFoundError("pipeline not found")
    chain = await _resolve_attached_chain(
        session,
        entity_kind="pipeline",
        entity_id=pipeline_id,
    )
    assert chain is not None
    direct_scopes, inherited_scopes = decision_learning_scopes_for_entity(
        entity_kind="pipeline",
        chain=chain,
    )
    decisions, learnings = await decision_learning_inheritance_lists(
        session,
        direct_scopes=direct_scopes,
        inherited_scopes=inherited_scopes,
    )
    return GetPipelineResponse(
        pipeline=pipeline_from_db(pipeline),
        decisions=decisions,
        learnings=learnings,
    )


async def create_pipeline(
    session: AsyncSession,
    request: CreatePipelineRequest,
    *,
    actor_id: UUID,
) -> CreatePipelineResponse:
    response: CreatePipelineResponse | None = None
    async with audited_op(
        session,
        op=CREATE_PIPELINE_OP,
        target_kind=PIPELINE_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        project = await session.get(DbProject, request.project_id)
        if project is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="project_not_found",
                message="project not found",
            )

        db_pipeline = DbPipeline(
            project_id=request.project_id,
            name=request.name,
            is_template=False,
            created_by_actor_id=actor_id,
        )
        session.add(db_pipeline)
        await session.flush()

        response = CreatePipelineResponse(pipeline=pipeline_from_db(db_pipeline))
        audit.target_id = db_pipeline.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def _clone_source_jobs(
    session: AsyncSession,
    *,
    source_pipeline: DbPipeline,
    cloned_pipeline: DbPipeline,
    actor_id: UUID,
) -> list[DbJob]:
    source_jobs = list(
        (
            await session.scalars(
                select(DbJob)
                .where(DbJob.pipeline_id == source_pipeline.id)
                .order_by(DbJob.created_at.asc(), DbJob.id.asc())
            )
        ).all()
    )
    cloned_jobs = [
        DbJob(
            pipeline_id=cloned_pipeline.id,
            project_id=source_pipeline.project_id,
            state=READY_STATE,
            title=source_job.title,
            description=source_job.description,
            contract=copy.deepcopy(source_job.contract),
            labels=list(source_job.labels),
            created_by_actor_id=actor_id,
        )
        for source_job in source_jobs
    ]
    session.add_all(cloned_jobs)
    await session.flush()
    return cloned_jobs


async def clone_pipeline(
    session: AsyncSession,
    source_id: UUID,
    request: ClonePipelineRequest,
    *,
    actor_id: UUID,
) -> ClonePipelineResponse:
    response: ClonePipelineResponse | None = None
    request_payload = {
        "source_id": str(source_id),
        "name": request.name,
    }
    async with audited_op(
        session,
        op=CLONE_PIPELINE_OP,
        target_kind=PIPELINE_TARGET_KIND,
        request_payload=request_payload,
    ) as audit:
        source_pipeline = await session.get(DbPipeline, source_id)
        if source_pipeline is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="pipeline_not_found",
                message="pipeline not found",
            )

        cloned_pipeline = DbPipeline(
            project_id=source_pipeline.project_id,
            name=request.name,
            is_template=False,
            cloned_from_pipeline_id=source_pipeline.id,
            created_by_actor_id=actor_id,
        )
        session.add(cloned_pipeline)
        await session.flush()

        cloned_jobs = await _clone_source_jobs(
            session,
            source_pipeline=source_pipeline,
            cloned_pipeline=cloned_pipeline,
            actor_id=actor_id,
        )

        response = ClonePipelineResponse(
            pipeline=pipeline_from_db(cloned_pipeline),
            jobs=[job_from_db(job) for job in cloned_jobs],
        )
        audit.target_id = cloned_pipeline.id
        audit.response_payload = {
            "cloned_pipeline_id": str(cloned_pipeline.id),
            "cloned_job_ids": [str(job.id) for job in cloned_jobs],
        }

    assert response is not None
    return response


async def archive_pipeline(
    session: AsyncSession,
    pipeline_id: UUID,
) -> ArchivePipelineResponse:
    response: ArchivePipelineResponse | None = None
    request_payload = {"pipeline_id": str(pipeline_id)}
    async with audited_op(
        session,
        op=ARCHIVE_PIPELINE_OP,
        target_kind=PIPELINE_TARGET_KIND,
        target_id=pipeline_id,
        request_payload=request_payload,
    ) as audit:
        db_pipeline = await session.get(DbPipeline, pipeline_id)
        if db_pipeline is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="pipeline_not_found",
                message="pipeline not found",
            )

        if db_pipeline.archived_at is None:
            db_pipeline.archived_at = datetime.now(UTC)
        await session.flush()

        response = ArchivePipelineResponse(pipeline=pipeline_from_db(db_pipeline))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def update_pipeline(
    session: AsyncSession,
    pipeline_id: UUID,
    request_payload: dict[str, object],
) -> UpdatePipelineResponse:
    response: UpdatePipelineResponse | None = None
    full_request_payload = {
        "pipeline_id": str(pipeline_id),
        **request_payload,
    }
    async with audited_op(
        session,
        op=UPDATE_PIPELINE_OP,
        target_kind=PIPELINE_TARGET_KIND,
        target_id=pipeline_id,
        request_payload=full_request_payload,
    ) as audit:
        db_pipeline = await session.get(DbPipeline, pipeline_id)
        if db_pipeline is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="pipeline_not_found",
                message="pipeline not found",
            )

        if "project_id" in request_payload:
            raise BusinessRuleException(
                status_code=400,
                error_code="project_id_immutable",
                message="pipeline project_id is immutable",
            )

        try:
            request = UpdatePipelineRequest.model_validate(request_payload)
        except ValidationError as exc:
            raise BusinessRuleException(
                status_code=422,
                error_code="invalid_pipeline_update",
                message="invalid pipeline update payload",
            ) from exc

        db_pipeline.name = request.name
        await session.flush()
        response = UpdatePipelineResponse(pipeline=pipeline_from_db(db_pipeline))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
