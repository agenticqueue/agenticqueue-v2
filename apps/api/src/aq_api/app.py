import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from aq_api._auth import (
    UnauthenticatedError,
    authenticate_request_context,
    current_actor,
    unauthenticated_response,
)
from aq_api._health import current_health_status
from aq_api._version import OPENAPI_VERSION, VERSION_INFO
from aq_api.mcp import mcp_http_app
from aq_api.models import HealthStatus, VersionInfo
from aq_api.routes.actors import router as actors_router
from aq_api.routes.api_keys import router as api_keys_router
from aq_api.routes.audit import router as audit_router
from aq_api.routes.decisions import router as decisions_router
from aq_api.routes.jobs import router as jobs_router
from aq_api.routes.labels import router as labels_router
from aq_api.routes.learnings import router as learnings_router
from aq_api.routes.pipelines import router as pipelines_router
from aq_api.routes.projects import router as projects_router
from aq_api.routes.setup import router as setup_router
from aq_api.services.claim_auto_release import (
    claim_auto_release_loop,
    ensure_system_actor,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _mcp_lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    async with mcp_http_app.lifespan(fastapi_app):
        yield


@asynccontextmanager
async def app_lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    async with _mcp_lifespan(fastapi_app):
        from aq_api._db import SessionLocal

        system_actor_id: UUID | None = None
        try:
            async with SessionLocal() as session:
                system_actor_id = await ensure_system_actor(session)
                await session.commit()
        except Exception as exc:
            logger.warning(
                "ensure_system_actor failed at startup; sweep loop will retry: %s",
                exc,
            )

        sweep_task = asyncio.create_task(claim_auto_release_loop(system_actor_id))
        try:
            yield
        finally:
            sweep_task.cancel()
            try:
                await sweep_task
            except asyncio.CancelledError:
                pass

# OpenAPI uses the same env-driven version path as the runtime `/version` surface.
app = FastAPI(
    title="AgenticQueue 2.0 API",
    version=OPENAPI_VERSION,
    lifespan=app_lifespan,
)


@app.exception_handler(UnauthenticatedError)
async def unauthenticated_exception_handler(
    _request: Request,
    _exc: UnauthenticatedError,
) -> JSONResponse:
    return unauthenticated_response()


@app.middleware("http")
async def require_mcp_bearer(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if not request.url.path.startswith("/mcp"):
        return await call_next(request)

    try:
        async with authenticate_request_context(request):
            return await call_next(request)
    except UnauthenticatedError:
        return unauthenticated_response()

    return unauthenticated_response()


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    return current_health_status()


@app.get(
    "/version",
    response_model=VersionInfo,
    dependencies=[Depends(current_actor)],
)
async def get_version() -> VersionInfo:
    return VERSION_INFO


app.include_router(setup_router)
app.include_router(actors_router)
app.include_router(api_keys_router)
app.include_router(audit_router)
app.include_router(projects_router)
app.include_router(labels_router)
app.include_router(pipelines_router)
app.include_router(jobs_router)
app.include_router(decisions_router)
app.include_router(learnings_router)


# app.mount("/mcp", mcp.http_app(path="/")) redirects POST /mcp to /mcp/.
# The C2 harness and ADR-AQ-021 pin the exact no-redirect /mcp path.
app.router.routes.extend(mcp_http_app.routes)

__all__ = ["app"]
