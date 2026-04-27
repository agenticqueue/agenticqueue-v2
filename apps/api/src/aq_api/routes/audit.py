from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._auth import current_actor
from aq_api.models import AuditLogPage, AuditQueryParams
from aq_api.models.db import Actor as DbActor
from aq_api.services.audit import InvalidAuditCursorError, query_audit_log

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


AuthenticatedActor = Annotated[DbActor, Depends(current_actor)]
SessionDep = Annotated[AsyncSession, Depends(_session_dependency)]


@router.get("/audit", response_model=AuditLogPage)
async def audit_query(
    _actor: AuthenticatedActor,
    session: SessionDep,
    actor: str | None = None,
    op: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: str | None = None,
) -> AuditLogPage | JSONResponse:
    try:
        params = AuditQueryParams.model_validate(
            {
                "actor": actor,
                "op": op,
                "since": since,
                "until": until,
                "limit": limit,
                "cursor": cursor,
            }
        )
        return await query_audit_log(session, params)
    except InvalidAuditCursorError:
        return JSONResponse({"error": "invalid_cursor"}, status_code=422)
    except ValidationError:
        return JSONResponse({"error": "invalid_audit_query"}, status_code=422)
