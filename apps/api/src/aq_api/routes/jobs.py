from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    CancelJobResponse,
    ClaimNextJobRequest,
    ClaimNextJobResponse,
    CommentOnJobRequest,
    CommentOnJobResponse,
    CreateJobRequest,
    CreateJobResponse,
    GetJobResponse,
    JobState,
    ListJobCommentsResponse,
    ListJobsResponse,
    ListReadyJobsResponse,
    ReleaseJobResponse,
    ResetClaimRequest,
    ResetClaimResponse,
    UpdateJobResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.models.labels import LabelName
from aq_api.services.claim import claim_next_job as claim_next_job_service
from aq_api.services.job_comments import (
    InvalidJobCommentCursorError,
    JobCommentJobNotFoundError,
)
from aq_api.services.job_comments import comment_on_job as comment_on_job_service
from aq_api.services.job_comments import list_job_comments as list_job_comments_service
from aq_api.services.job_lifecycle import cancel_job as cancel_job_service
from aq_api.services.jobs import InvalidJobCursorError, JobNotFoundError
from aq_api.services.jobs import create_job as create_job_service
from aq_api.services.jobs import get_job as get_job_service
from aq_api.services.jobs import list_jobs as list_jobs_service
from aq_api.services.jobs import update_job as update_job_service
from aq_api.services.list_ready_jobs import InvalidReadyJobCursorError
from aq_api.services.list_ready_jobs import list_ready_jobs as list_ready_jobs_service
from aq_api.services.release import release_job as release_job_service
from aq_api.services.release import reset_claim as reset_claim_service

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


@router.post("/jobs/claim", response_model=ClaimNextJobResponse)
async def claim_next_job(
    request: ClaimNextJobRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> ClaimNextJobResponse | JSONResponse:
    try:
        return await claim_next_job_service(session, request=request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/jobs/{job_id}/comments", response_model=CommentOnJobResponse)
async def comment_on_job(
    job_id: UUID,
    request: CommentOnJobRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CommentOnJobResponse | JSONResponse:
    try:
        return await comment_on_job_service(
            session,
            job_id,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.get("/jobs/{job_id}/comments", response_model=ListJobCommentsResponse)
async def list_job_comments(
    job_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ListJobCommentsResponse | JSONResponse:
    try:
        return await list_job_comments_service(
            session,
            job_id,
            limit=limit,
            cursor=cursor,
        )
    except JobCommentJobNotFoundError:
        return JSONResponse({"error": "job_not_found"}, status_code=404)
    except InvalidJobCommentCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.post("/jobs/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(
    job_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> CancelJobResponse | JSONResponse:
    try:
        return await cancel_job_service(session, job_id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/jobs/{job_id}/release", response_model=ReleaseJobResponse)
async def release_job(
    job_id: UUID,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> ReleaseJobResponse | JSONResponse:
    try:
        return await release_job_service(session, job_id=job_id, actor_id=actor.id)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/jobs/{job_id}/reset-claim", response_model=ResetClaimResponse)
async def reset_claim(
    job_id: UUID,
    request: ResetClaimRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> ResetClaimResponse | JSONResponse:
    try:
        return await reset_claim_service(
            session,
            job_id=job_id,
            request=request,
            actor_id=actor.id,
        )
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


@router.get("/jobs/ready", response_model=ListReadyJobsResponse)
async def list_ready_jobs(
    _actor: AuthenticatedActor,
    session: SessionDep,
    project_id: Annotated[UUID, Query(alias="project")],
    label_filter: Annotated[list[LabelName] | None, Query(alias="label")] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
) -> ListReadyJobsResponse | JSONResponse:
    try:
        return await list_ready_jobs_service(
            session,
            project_id=project_id,
            label_filter=label_filter,
            limit=limit,
            cursor=cursor,
        )
    except InvalidReadyJobCursorError:
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
