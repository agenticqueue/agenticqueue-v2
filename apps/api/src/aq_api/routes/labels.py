from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    AttachLabelRequest,
    AttachLabelResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    RegisterLabelRequest,
    RegisterLabelResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.labels import attach_label as attach_label_service
from aq_api.services.labels import detach_label as detach_label_service
from aq_api.services.labels import register_label as register_label_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/projects/{project_id}/labels", response_model=RegisterLabelResponse)
async def register_label(
    project_id: UUID,
    request: RegisterLabelRequest,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> RegisterLabelResponse | JSONResponse:
    try:
        return await register_label_service(session, project_id, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.post("/jobs/{job_id}/labels", response_model=AttachLabelResponse)
async def attach_label(
    job_id: UUID,
    request: AttachLabelRequest,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> AttachLabelResponse | JSONResponse:
    try:
        return await attach_label_service(session, job_id, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)


@router.delete("/jobs/{job_id}/labels/{name}", response_model=DetachLabelResponse)
async def detach_label(
    job_id: UUID,
    name: str,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> DetachLabelResponse | JSONResponse:
    try:
        request = DetachLabelRequest(label_name=name)
        return await detach_label_service(session, job_id, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
    except ValidationError:
        return JSONResponse({"error": "invalid_label_name"}, status_code=422)
