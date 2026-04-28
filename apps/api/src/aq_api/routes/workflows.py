from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    ArchiveWorkflowResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    GetWorkflowResponse,
    ListWorkflowsResponse,
    UpdateWorkflowRequest,
    UpdateWorkflowResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.workflows import (
    InvalidWorkflowCursorError,
    WorkflowNotFoundError,
)
from aq_api.services.workflows import (
    archive_workflow as archive_workflow_service,
)
from aq_api.services.workflows import (
    create_workflow as create_workflow_service,
)
from aq_api.services.workflows import (
    get_workflow as get_workflow_service,
)
from aq_api.services.workflows import (
    list_workflows as list_workflows_service,
)
from aq_api.services.workflows import (
    update_workflow as update_workflow_service,
)

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/workflows", response_model=CreateWorkflowResponse)
async def create_workflow(
    request: CreateWorkflowRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateWorkflowResponse | JSONResponse:
    try:
        return await create_workflow_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.get("/workflows", response_model=ListWorkflowsResponse)
async def list_workflows(
    _actor: AuthenticatedActor,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    include_archived: bool = False,
) -> ListWorkflowsResponse | JSONResponse:
    try:
        return await list_workflows_service(
            session,
            limit=limit,
            cursor=cursor,
            include_archived=include_archived,
        )
    except InvalidWorkflowCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.get("/workflows/{workflow_id}", response_model=GetWorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetWorkflowResponse | JSONResponse:
    try:
        return await get_workflow_service(session, workflow_id)
    except WorkflowNotFoundError:
        return JSONResponse({"error": "workflow_not_found"}, status_code=404)


@router.patch("/workflows/{workflow_id}", response_model=UpdateWorkflowResponse)
async def update_workflow(
    workflow_id: UUID,
    request: UpdateWorkflowRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdateWorkflowResponse | JSONResponse:
    try:
        return await update_workflow_service(
            session,
            workflow_id,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/workflows/{slug}/archive", response_model=ArchiveWorkflowResponse)
async def archive_workflow(
    slug: str,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> ArchiveWorkflowResponse | JSONResponse:
    try:
        return await archive_workflow_service(session, slug)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
