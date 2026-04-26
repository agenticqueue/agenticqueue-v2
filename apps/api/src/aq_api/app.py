from datetime import UTC, datetime

from fastapi import FastAPI

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
    return HealthStatus(status="ok", timestamp=datetime.now(UTC))


@app.get("/version", response_model=VersionInfo)
def get_version() -> VersionInfo:
    return VERSION_INFO


app.router.routes.extend(mcp_http_app.routes)

__all__ = ["app"]
