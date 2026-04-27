from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    ArchiveProjectResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    GetProjectResponse,
    ListProjectsResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.projects import (
    InvalidProjectCursorError,
    ProjectNotFoundError,
)
from aq_api.services.projects import (
    archive_project as archive_project_service,
)
from aq_api.services.projects import (
    create_project as create_project_service,
)
from aq_api.services.projects import (
    get_project as get_project_service,
)
from aq_api.services.projects import (
    list_projects as list_projects_service,
)
from aq_api.services.projects import (
    update_project as update_project_service,
)

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/projects", response_model=CreateProjectResponse)
async def create_project(
    request: CreateProjectRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateProjectResponse | JSONResponse:
    try:
        return await create_project_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.get("/projects", response_model=ListProjectsResponse)
async def list_projects(
    _actor: AuthenticatedActor,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    include_archived: bool = False,
) -> ListProjectsResponse | JSONResponse:
    try:
        return await list_projects_service(
            session,
            limit=limit,
            cursor=cursor,
            include_archived=include_archived,
        )
    except InvalidProjectCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.get("/projects/{project_id}", response_model=GetProjectResponse)
async def get_project(
    project_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetProjectResponse | JSONResponse:
    try:
        return await get_project_service(session, project_id)
    except ProjectNotFoundError:
        return JSONResponse({"error": "project_not_found"}, status_code=404)


@router.patch("/projects/{project_id}", response_model=UpdateProjectResponse)
async def update_project(
    project_id: UUID,
    request: UpdateProjectRequest,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdateProjectResponse | JSONResponse:
    try:
        return await update_project_service(session, project_id, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/projects/{project_id}/archive", response_model=ArchiveProjectResponse)
async def archive_project(
    project_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> ArchiveProjectResponse | JSONResponse:
    try:
        return await archive_project_service(session, project_id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
