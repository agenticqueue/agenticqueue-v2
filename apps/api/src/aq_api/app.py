import os
import subprocess
from datetime import UTC, datetime

from fastapi import FastAPI

from aq_api._datetime import parse_utc
from aq_api.models import HealthStatus, VersionInfo

AQ_VERSION_ENV = "AQ_VERSION"
AQ_GIT_COMMIT_ENV = "AQ_GIT_COMMIT"
AQ_BUILT_AT_ENV = "AQ_BUILT_AT"
DEFAULT_VERSION = "0.0.0-dev"
FALLBACK_COMMIT = "0000000"
OPENAPI_VERSION = os.getenv(AQ_VERSION_ENV, DEFAULT_VERSION)


def _git_short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return FALLBACK_COMMIT
    return result.stdout.strip() or FALLBACK_COMMIT


def _load_version_info() -> VersionInfo:
    built_at = os.getenv(AQ_BUILT_AT_ENV)
    return VersionInfo(
        version=os.getenv(AQ_VERSION_ENV, DEFAULT_VERSION),
        commit=os.getenv(AQ_GIT_COMMIT_ENV, _git_short_sha()),
        built_at=parse_utc(built_at) if built_at else datetime.now(UTC),
    )


VERSION_INFO = _load_version_info()

# OpenAPI uses the same env-driven version path as the runtime `/version` surface.
app = FastAPI(title="AgenticQueue 2.0 API", version=OPENAPI_VERSION)


@app.get("/healthz", response_model=HealthStatus)
def healthz() -> HealthStatus:
    return HealthStatus(status="ok", timestamp=datetime.now(UTC))


@app.get("/version", response_model=VersionInfo)
def get_version() -> VersionInfo:
    return VERSION_INFO


__all__ = ["app"]
