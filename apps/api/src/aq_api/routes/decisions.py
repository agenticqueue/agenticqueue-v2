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
    CreateDecisionRequest,
    CreateDecisionResponse,
    GetDecisionResponse,
    ListDecisionsResponse,
    SupersedeDecisionRequest,
    SupersedeDecisionResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.routes._errors import business_rule_response
from aq_api.services._artifacts import InvalidArtifactCursorError
from aq_api.services.decisions import DecisionNotFoundError
from aq_api.services.decisions import create_decision as create_decision_service
from aq_api.services.decisions import get_decision as get_decision_service
from aq_api.services.decisions import list_decisions as list_decisions_service
from aq_api.services.decisions import (
    supersede_decision as supersede_decision_service,
)

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/decisions", response_model=CreateDecisionResponse)
async def create_decision(
    request: CreateDecisionRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateDecisionResponse | JSONResponse:
    try:
        return await create_decision_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return business_rule_response(exc)


@router.get("/decisions", response_model=ListDecisionsResponse)
async def list_decisions(
    _actor: AuthenticatedActor,
    session: SessionDep,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListDecisionsResponse | JSONResponse:
    try:
        return await list_decisions_service(
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


@router.get("/decisions/{decision_id}", response_model=GetDecisionResponse)
async def get_decision(
    decision_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetDecisionResponse | JSONResponse:
    try:
        return await get_decision_service(session, decision_id)
    except DecisionNotFoundError:
        return JSONResponse({"error": "decision_not_found"}, status_code=404)


@router.post(
    "/decisions/{decision_id}/supersede",
    response_model=SupersedeDecisionResponse,
)
async def supersede_decision(
    decision_id: UUID,
    request: SupersedeDecisionRequest,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> SupersedeDecisionResponse | JSONResponse:
    try:
        return await supersede_decision_service(session, decision_id, request)
    except BusinessRuleException as exc:
        return business_rule_response(exc)
