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
    CreateComponentRequest,
    CreateComponentResponse,
    GetComponentResponse,
    ListComponentsResponse,
    UpdateComponentRequest,
    UpdateComponentResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.routes._errors import business_rule_response
from aq_api.services._artifacts import InvalidArtifactCursorError
from aq_api.services.components import ComponentNotFoundError
from aq_api.services.components import create_component as create_component_service
from aq_api.services.components import get_component as get_component_service
from aq_api.services.components import list_components as list_components_service
from aq_api.services.components import update_component as update_component_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/components", response_model=CreateComponentResponse)
async def create_component(
    request: CreateComponentRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateComponentResponse | JSONResponse:
    try:
        return await create_component_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return business_rule_response(exc)


@router.get("/components", response_model=ListComponentsResponse)
async def list_components(
    _actor: AuthenticatedActor,
    session: SessionDep,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListComponentsResponse | JSONResponse:
    try:
        return await list_components_service(
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


@router.get("/components/{component_id}", response_model=GetComponentResponse)
async def get_component(
    component_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetComponentResponse | JSONResponse:
    try:
        return await get_component_service(session, component_id)
    except ComponentNotFoundError:
        return JSONResponse({"error": "component_not_found"}, status_code=404)


@router.patch("/components/{component_id}", response_model=UpdateComponentResponse)
async def update_component(
    component_id: UUID,
    request: UpdateComponentRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> UpdateComponentResponse | JSONResponse:
    try:
        return await update_component_service(
            session,
            component_id,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return business_rule_response(exc)
