from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._auth import current_actor
from aq_api.models import RevokeApiKeyResponse
from aq_api.models.db import Actor as DbActor
from aq_api.services.api_keys import revoke_api_key as revoke_api_key_service

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.delete("/api-keys/{api_key_id}", response_model=RevokeApiKeyResponse)
async def revoke_api_key(
    api_key_id: UUID,
    actor: AuthenticatedActor,
    session: SessionDep,
) -> RevokeApiKeyResponse | JSONResponse:
    try:
        return await revoke_api_key_service(
            session,
            actor_id=actor.id,
            api_key_id=api_key_id,
        )
    except BusinessRuleException as exc:
        return JSONResponse({"error": exc.error_code}, status_code=exc.status_code)
