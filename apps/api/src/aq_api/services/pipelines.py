import base64
import json
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    CreatePipelineRequest,
    CreatePipelineResponse,
    GetPipelineResponse,
    ListPipelinesResponse,
    Pipeline,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
)
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CREATE_PIPELINE_OP = "create_pipeline"
UPDATE_PIPELINE_OP = "update_pipeline"
PIPELINE_TARGET_KIND = "pipeline"


class InvalidPipelineCursorError(Exception):
    pass


class PipelineNotFoundError(Exception):
    pass


def pipeline_from_db(pipeline: DbPipeline) -> Pipeline:
    return Pipeline(
        id=pipeline.id,
        project_id=pipeline.project_id,
        name=pipeline.name,
        instantiated_from_workflow_id=pipeline.instantiated_from_workflow_id,
        instantiated_from_workflow_version=pipeline.instantiated_from_workflow_version,
        created_at=pipeline.created_at,
        created_by_actor_id=pipeline.created_by_actor_id,
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
    statement = select(DbPipeline)

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
    return GetPipelineResponse(pipeline=pipeline_from_db(pipeline))


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
            instantiated_from_workflow_id=None,
            instantiated_from_workflow_version=None,
            created_by_actor_id=actor_id,
        )
        session.add(db_pipeline)
        await session.flush()

        response = CreatePipelineResponse(pipeline=pipeline_from_db(db_pipeline))
        audit.target_id = db_pipeline.id
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
