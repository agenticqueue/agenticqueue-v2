from fastapi import FastAPI

from aq_api._health import current_health_status
from aq_api._version import OPENAPI_VERSION, VERSION_INFO
from aq_api.mcp import mcp_http_app
from aq_api.models import HealthStatus, VersionInfo

# OpenAPI uses the same env-driven version path as the runtime `/version` surface.
app = FastAPI(
    title="AgenticQueue 2.0 API",
    version=OPENAPI_VERSION,
    lifespan=mcp_http_app.lifespan,
)


@app.get("/healthz", response_model=HealthStatus)
def healthz() -> HealthStatus:
    return current_health_status()


@app.get("/version", response_model=VersionInfo)
def get_version() -> VersionInfo:
    return VERSION_INFO


# app.mount("/mcp", mcp.http_app(path="/")) redirects POST /mcp to /mcp/.
# The C2 harness and ADR-AQ-021 pin the exact no-redirect /mcp path.
app.router.routes.extend(mcp_http_app.routes)

__all__ = ["app"]
