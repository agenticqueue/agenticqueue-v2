from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    CreateObjectiveRequest,
    CreateObjectiveResponse,
    GetObjectiveResponse,
    ListObjectivesResponse,
    UpdateObjectiveRequest,
    UpdateObjectiveResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.routes._errors import business_rule_response
from aq_api.services._artifacts import InvalidArtifactCursorError
from aq_api.services.objectives import ObjectiveNotFoundError
from aq_api.services.objectives import create_objective as create_objective_service
from aq_api.services.objectives import get_objective as get_objective_service
from aq_api.services.objectives import list_objectives as list_objectives_service
from aq_api.services.objectives import update_objective as update_objective_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/objectives", response_model=CreateObjectiveResponse)
async def create_objective(
    request: CreateObjectiveRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateObjectiveResponse | JSONResponse:
    try:
        return await create_objective_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return business_rule_response(exc)


@router.get("/objectives", response_model=ListObjectivesResponse)
async def list_objectives(
    _actor: AuthenticatedActor,
    session: SessionDep,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListObjectivesResponse | JSONResponse:
    try:
        return await list_objectives_service(
            session,
            attached_to_kind=attached_to_kind,
            attached_to_id=attached_to_id,
            actor_id=actor_id,
            since=since,
            limit=limit,
            cursor=cursor,
            include_deactivated=include_deactivated,
        )
    except InvalidArtifactCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.get("/objectives/{objective_id}", response_model=GetObjectiveResponse)
async def get_objective(
    objective_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetObjectiveResponse | JSONResponse:
    try:
        return await get_objective_service(session, objective_id)
    except ObjectiveNotFoundError:
        return JSONResponse({"error": "objective_not_found"}, status_code=404)


@router.patch("/objectives/{objective_id}", response_model=UpdateObjectiveResponse)
async def update_objective(
    objective_id: UUID,
    request: UpdateObjectiveRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdateObjectiveResponse | JSONResponse:
    try:
        return await update_objective_service(
            session,
            objective_id,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return business_rule_response(exc)
