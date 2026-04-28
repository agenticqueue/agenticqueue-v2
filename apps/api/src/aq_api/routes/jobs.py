from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    CreateJobRequest,
    CreateJobResponse,
    GetJobResponse,
    JobState,
    ListJobsResponse,
    UpdateJobResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.jobs import InvalidJobCursorError, JobNotFoundError
from aq_api.services.jobs import create_job as create_job_service
from aq_api.services.jobs import get_job as get_job_service
from aq_api.services.jobs import list_jobs as list_jobs_service
from aq_api.services.jobs import update_job as update_job_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    request: CreateJobRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateJobResponse | JSONResponse:
    try:
        return await create_job_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.get("/jobs", response_model=ListJobsResponse)
async def list_jobs(
    _actor: AuthenticatedActor,
    session: SessionDep,
    project_id: UUID | None = None,
    pipeline_id: UUID | None = None,
    state: JobState | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ListJobsResponse | JSONResponse:
    try:
        return await list_jobs_service(
            session,
            project_id=project_id,
            pipeline_id=pipeline_id,
            state=state,
            limit=limit,
            cursor=cursor,
        )
    except InvalidJobCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.get("/jobs/{job_id}", response_model=GetJobResponse)
async def get_job(
    job_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetJobResponse | JSONResponse:
    try:
        return await get_job_service(session, job_id)
    except JobNotFoundError:
        return JSONResponse({"error": "job_not_found"}, status_code=404)


@router.patch("/jobs/{job_id}", response_model=UpdateJobResponse)
async def update_job(
    job_id: UUID,
    request: dict[str, object],
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdateJobResponse | JSONResponse:
    try:
        return await update_job_service(session, job_id, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
