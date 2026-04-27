from collections.abc import Awaitable, Callable

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
from aq_api.routes.setup import router as setup_router

# OpenAPI uses the same env-driven version path as the runtime `/version` surface.
app = FastAPI(
    title="AgenticQueue 2.0 API",
    version=OPENAPI_VERSION,
    lifespan=mcp_http_app.lifespan,
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
        async for _actor in authenticate_request_context(request):
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


# app.mount("/mcp", mcp.http_app(path="/")) redirects POST /mcp to /mcp/.
# The C2 harness and ADR-AQ-021 pin the exact no-redirect /mcp path.
app.router.routes.extend(mcp_http_app.routes)

__all__ = ["app"]
