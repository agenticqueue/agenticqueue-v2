from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.models.db import Actor as DbActor
from aq_api.services.auth import resolve_actor

UNAUTHENTICATED_BODY = {"error": "unauthenticated"}


class UnauthenticatedError(Exception):
    pass


def unauthenticated_response() -> JSONResponse:
    return JSONResponse(UNAUTHENTICATED_BODY, status_code=401)


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator == "" or scheme.lower() != "bearer" or token == "":
        return None
    return token


async def authenticate_bearer(
    session: AsyncSession,
    authorization: str | None,
) -> DbActor:
    token = extract_bearer_token(authorization)
    if token is None:
        raise UnauthenticatedError

    actor = await resolve_actor(session, token)
    if actor is None:
        raise UnauthenticatedError

    return actor


async def current_actor(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AsyncIterator[DbActor]:
    from aq_api._db import SessionLocal

    async with SessionLocal() as session:
        actor = await authenticate_bearer(session, authorization)

    context_token = set_authenticated_actor_id(actor.id)
    try:
        yield actor
    finally:
        reset_authenticated_actor_id(context_token)


@asynccontextmanager
async def authenticate_request_context(request: Request) -> AsyncIterator[DbActor]:
    from aq_api._db import SessionLocal

    async with SessionLocal() as session:
        actor = await authenticate_bearer(
            session,
            request.headers.get("authorization"),
        )
        context_token = set_authenticated_actor_id(actor.id)
        try:
            yield actor
        finally:
            reset_authenticated_actor_id(context_token)
