import os
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
import pytest

ARTIFACT_DIR = Path(os.getenv("AQ_ARTIFACT_DIR", "plans/v2-rebuild/artifacts/cap-02"))
TOKEN_RE = re.compile(r"\baq2_[A-Za-z0-9_-]{20,}\b")
ARGON2_RE = re.compile(r"\$argon2id\$[^\s\"'<>]+")
UUID_RE = re.compile(
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"
)
LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Regenerate parity snapshots from the running API/MCP surfaces.",
    )


@pytest.fixture
def update_snapshots(pytestconfig: pytest.Config) -> bool:
    return bool(pytestconfig.getoption("--update-snapshots"))


@pytest.fixture
def artifact_dir() -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_DIR


def _default_api_url() -> str:
    if Path("/.dockerenv").exists():
        return "http://127.0.0.1:8000"
    return "http://localhost:8001"


@pytest.fixture
def api_base_url() -> str:
    return os.getenv("AQ_API_URL", _default_api_url()).rstrip("/")


@pytest.fixture
def mcp_base_url(api_base_url: str) -> str:
    return f"{api_base_url}/mcp"


@pytest.fixture
def db_url() -> str | None:
    return os.getenv("DATABASE_URL")


@pytest.fixture
def db_url_sync() -> str | None:
    return os.getenv("DATABASE_URL_SYNC")


@pytest.fixture
def web_base_url() -> str:
    return os.getenv("AQ_WEB_URL", "http://127.0.0.1:3002").rstrip("/")


def _compose_command(*args: str) -> list[str]:
    command = ["docker", "compose"]
    env_file = os.getenv("AQ_COMPOSE_ENV_FILE")
    if env_file:
        command.extend(["--env-file", env_file])
    project = os.getenv("AQ_COMPOSE_PROJECT")
    if project:
        command.extend(["-p", project])
    command.extend(args)
    return command


def _truncate_sql() -> str:
    return (
        "DELETE FROM audit_log; "
        "DELETE FROM job_comments; "
        "DELETE FROM job_edges; "
        "DELETE FROM jobs; "
        "DELETE FROM pipelines; "
        "DELETE FROM labels; "
        "DELETE FROM projects; "
        "DELETE FROM api_keys; "
        "DELETE FROM actors;"
    )


def _direct_conninfo() -> str | None:
    value = os.getenv("DATABASE_URL_SYNC")
    if not value:
        return None
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def truncate_db() -> Callable[[], None]:
    def truncate() -> None:
        conninfo = _direct_conninfo()
        if conninfo is not None:
            try:
                with psycopg.connect(conninfo, autocommit=True) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(_truncate_sql())
                return
            except psycopg.OperationalError:
                pass

        database = os.getenv("POSTGRES_DB", "aq2")
        subprocess.run(
            _compose_command(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "aq",
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                _truncate_sql(),
            ),
            check=True,
            capture_output=True,
            text=True,
        )

    return truncate


@pytest.fixture
def founder(
    api_base_url: str,
    truncate_db: Callable[[], None],
) -> Iterator[dict[str, str]]:
    truncate_db()
    response = httpx.post(f"{api_base_url}/setup", json={}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict)
    actor_id = str(payload["actor_id"])
    founder_key = str(payload["founder_key"])
    UUID(actor_id)
    assert founder_key.startswith("aq2_")
    yield {"actor_id": actor_id, "key": founder_key}
    truncate_db()


@pytest.fixture
def founder_key(founder: dict[str, str]) -> str:
    return founder["key"]


@pytest.fixture
def founder_actor_id(founder: dict[str, str]) -> str:
    return founder["actor_id"]


@pytest.fixture
def auth_headers(founder_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {founder_key}"}


@pytest.fixture
def redact_evidence() -> Callable[[str], str]:
    def redact(value: str) -> str:
        redacted = ARGON2_RE.sub("[ARGON2_REDACTED]", value)
        redacted = TOKEN_RE.sub("[TOKEN_REDACTED]", redacted)
        redacted = UUID_RE.sub("[UUID_REDACTED]", redacted)
        redacted = LONG_SECRET_RE.sub("[TOKEN_REDACTED]", redacted)
        return redacted

    return redact


@pytest.fixture(autouse=True)
def _docker_playwright_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PLAYWRIGHT_USE_DOCKER", "1")
    yield
