from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models import SetupRequest, SetupResponse
from aq_api.services.setup import AlreadySetupError, run_setup

ALREADY_SETUP_BODY = {"error": "already_setup"}

router = APIRouter()


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    from aq_api._db import get_session

    async for session in get_session():
        yield session


@router.post("/setup", response_model=SetupResponse)
async def setup(
    _request: SetupRequest,
    session: Annotated[AsyncSession, Depends(_session_dependency)],
) -> SetupResponse | JSONResponse:
    try:
        return await run_setup(session)
    except AlreadySetupError:
        return JSONResponse(ALREADY_SETUP_BODY, status_code=409)
