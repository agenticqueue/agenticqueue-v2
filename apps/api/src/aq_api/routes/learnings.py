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
    EditLearningRequest,
    EditLearningResponse,
    GetLearningResponse,
    ListLearningsResponse,
    SubmitLearningRequest,
    SubmitLearningResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.routes._errors import business_rule_response
from aq_api.services._artifacts import InvalidArtifactCursorError
from aq_api.services.learnings import LearningNotFoundError
from aq_api.services.learnings import edit_learning as edit_learning_service
from aq_api.services.learnings import get_learning as get_learning_service
from aq_api.services.learnings import list_learnings as list_learnings_service
from aq_api.services.learnings import submit_learning as submit_learning_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.post("/learnings", response_model=SubmitLearningResponse)
async def submit_learning(
    request: SubmitLearningRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> SubmitLearningResponse | JSONResponse:
    try:
        return await submit_learning_service(session, request, actor_id=actor.id)
    except BusinessRuleException as exc:
        return business_rule_response(exc)


@router.get("/learnings", response_model=ListLearningsResponse)
async def list_learnings(
    _actor: AuthenticatedActor,
    session: SessionDep,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListLearningsResponse | JSONResponse:
    try:
        return await list_learnings_service(
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


@router.get("/learnings/{learning_id}", response_model=GetLearningResponse)
async def get_learning(
    learning_id: UUID,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> GetLearningResponse | JSONResponse:
    try:
        return await get_learning_service(session, learning_id)
    except LearningNotFoundError:
        return JSONResponse({"error": "learning_not_found"}, status_code=404)


@router.patch("/learnings/{learning_id}", response_model=EditLearningResponse)
async def edit_learning(
    learning_id: UUID,
    request: EditLearningRequest,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> EditLearningResponse | JSONResponse:
    try:
        return await edit_learning_service(
            session,
            learning_id,
            request,
            actor_id=actor.id,
        )
    except BusinessRuleException as exc:
        return business_rule_response(exc)
