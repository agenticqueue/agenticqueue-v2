import os
import re
import subprocess
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)

ARTIFACT_DIR = Path(os.getenv("AQ_ARTIFACT_DIR", "plans/v2-rebuild/artifacts/cap-02"))
PARITY_ACTOR_PREFIX = "parity-test-"
PARITY_PROJECT_SLUG_PREFIX = "parity-"
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
        "DELETE FROM audit_log "
        "WHERE authenticated_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%'); "
        "DELETE FROM decisions "
        "WHERE created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR (attached_to_kind = 'job' AND attached_to_id IN ("
        "SELECT jobs.id FROM jobs JOIN projects ON projects.id = jobs.project_id "
        f"WHERE projects.slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR projects.created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%'))); "
        "DELETE FROM learnings "
        "WHERE created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR (attached_to_kind = 'job' AND attached_to_id IN ("
        "SELECT jobs.id FROM jobs JOIN projects ON projects.id = jobs.project_id "
        f"WHERE projects.slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR projects.created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%'))); "
        "DELETE FROM job_comments "
        "WHERE author_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR job_id IN ("
        "SELECT jobs.id FROM jobs JOIN projects ON projects.id = jobs.project_id "
        f"WHERE projects.slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR projects.created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')); "
        "DELETE FROM job_edges "
        "WHERE from_job_id IN ("
        "SELECT jobs.id FROM jobs JOIN projects ON projects.id = jobs.project_id "
        f"WHERE projects.slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR projects.created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')) "
        "OR to_job_id IN ("
        "SELECT jobs.id FROM jobs JOIN projects ON projects.id = jobs.project_id "
        f"WHERE projects.slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR projects.created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')); "
        "DELETE FROM jobs "
        "WHERE created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR claimed_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR project_id IN ("
        "SELECT id FROM projects "
        f"WHERE slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')); "
        "DELETE FROM labels "
        "WHERE project_id IN ("
        "SELECT id FROM projects "
        f"WHERE slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')); "
        "DELETE FROM pipelines "
        "WHERE created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR project_id IN ("
        "SELECT id FROM projects "
        f"WHERE slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%')); "
        "DELETE FROM projects "
        f"WHERE slug LIKE '{PARITY_PROJECT_SLUG_PREFIX}%' "
        "OR created_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%'); "
        "DELETE FROM api_keys "
        "WHERE actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%') "
        "OR revoked_by_actor_id IN ("
        f"SELECT id FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%'); "
        f"DELETE FROM actors WHERE name LIKE '{PARITY_ACTOR_PREFIX}%';"
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


def _insert_parity_actor() -> dict[str, str]:
    conninfo = _direct_conninfo()
    if conninfo is None:
        pytest.skip("DATABASE_URL_SYNC is required for scoped parity actor setup")

    actor_name = f"{PARITY_ACTOR_PREFIX}founder-{uuid.uuid4().hex[:12]}"
    actor_key = f"aq2_parity_contract_{uuid.uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO actors (name, kind)
                VALUES (%s, 'human')
                RETURNING id
                """,
                (actor_name,),
            )
            row = cursor.fetchone()
            assert row is not None
            actor_id = str(row[0])
            UUID(actor_id)
            cursor.execute(
                """
                INSERT INTO api_keys
                    (actor_id, name, key_hash, prefix, lookup_id)
                VALUES
                    (%s, %s, %s, %s, %s)
                """,
                (
                    actor_id,
                    f"{PARITY_ACTOR_PREFIX}key-{uuid.uuid4()}",
                    PASSWORD_HASHER.hash(actor_key),
                    actor_key[:DISPLAY_PREFIX_LENGTH],
                    lookup_id_for_key(actor_key),
                ),
            )

    return {"actor_id": actor_id, "key": actor_key}


@pytest.fixture
def founder(
    truncate_db: Callable[[], None],
) -> Iterator[dict[str, str]]:
    truncate_db()
    yield _insert_parity_actor()
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
