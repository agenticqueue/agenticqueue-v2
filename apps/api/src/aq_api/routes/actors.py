from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import (
    CreateActorRequest,
    CreateActorResponse,
    ListActorsResponse,
    WhoamiResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.actors import (
    InvalidCursorError,
    get_self,
    list_actors,
)
from aq_api.services.actors import (
    create_actor as create_actor_service,
)

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.get("/actors/me", response_model=WhoamiResponse)
async def whoami(actor: AuthenticatedActor) -> WhoamiResponse:
    return get_self(actor)


@router.get("/actors", response_model=ListActorsResponse)
async def actor_list(
    _actor: AuthenticatedActor,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListActorsResponse | JSONResponse:
    try:
        return await list_actors(
            session,
            limit=limit,
            cursor=cursor,
            include_deactivated=include_deactivated,
        )
    except InvalidCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)


@router.post("/actors", response_model=CreateActorResponse)
async def create_actor(
    request: CreateActorRequest,
    _actor: AuthenticatedActor,
    session: SessionDep,
) -> CreateActorResponse | JSONResponse:
    try:
        return await create_actor_service(session, request)
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
