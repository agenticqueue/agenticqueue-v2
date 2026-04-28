from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    CreatePipelineRequest,
    CreatePipelineResponse,
    GetPipelineResponse,
    InstantiatePipelineRequest,
    InstantiatePipelineResponse,
    ListPipelinesResponse,
    UpdatePipelineResponse,
)
from aq_api.models.auth import AQModel
from aq_api.models.db import Actor as DbActor
from aq_api.models.pipelines import PipelineName
from aq_api.services.instantiate import (
    instantiate_pipeline as instantiate_pipeline_service,
)
from aq_api.services.pipelines import (
    InvalidPipelineCursorError,
    PipelineNotFoundError,
)
from aq_api.services.pipelines import create_pipeline as create_pipeline_service
from aq_api.services.pipelines import get_pipeline as get_pipeline_service
from aq_api.services.pipelines import list_pipelines as list_pipelines_service
from aq_api.services.pipelines import update_pipeline as update_pipeline_service

router = APIRouter()


class UpdatePipelinePayload(AQModel):
    name: PipelineName
    project_id: UUID | None = None


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/pipelines", response_model=CreatePipelineResponse)
async def create_pipeline(
    request: CreatePipelineRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreatePipelineResponse | JSONResponse:
    try:
        return await create_pipeline_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post(
    "/pipelines/from-workflow/{workflow_slug}",
    response_model=InstantiatePipelineResponse,
)
async def instantiate_pipeline(
    workflow_slug: str,
    request: InstantiatePipelineRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> InstantiatePipelineResponse | JSONResponse:
    try:
        return await instantiate_pipeline_service(
            session,
            workflow_slug,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.get("/pipelines", response_model=ListPipelinesResponse)
async def list_pipelines(
    _actor: AuthenticatedActor,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> ListPipelinesResponse | JSONResponse:
    try:
        return await list_pipelines_service(
            session,
            limit=limit,
            cursor=cursor,
        )
    except InvalidPipelineCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.get("/pipelines/{pipeline_id}", response_model=GetPipelineResponse)
async def get_pipeline(
    pipeline_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetPipelineResponse | JSONResponse:
    try:
        return await get_pipeline_service(session, pipeline_id)
    except PipelineNotFoundError:
        return JSONResponse({"error": "pipeline_not_found"}, status_code=404)


@router.patch("/pipelines/{pipeline_id}", response_model=UpdatePipelineResponse)
async def update_pipeline(
    pipeline_id: UUID,
    request: UpdatePipelinePayload,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdatePipelineResponse | JSONResponse:
    try:
        return await update_pipeline_service(
            session,
            pipeline_id,
            request.model_dump(mode="json", exclude_unset=True),
        )
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
