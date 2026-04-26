import os
from collections.abc import Iterator
from pathlib import Path

import pytest

ARTIFACT_DIR = Path("plans/v2-rebuild/artifacts/cap-01")


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


@pytest.fixture
def api_base_url() -> str:
    return os.getenv("AQ_API_URL", "http://localhost:8001").rstrip("/")


@pytest.fixture
def mcp_base_url(api_base_url: str) -> str:
    return f"{api_base_url}/mcp"


@pytest.fixture
def web_base_url() -> str:
    return os.getenv("AQ_WEB_URL", "http://127.0.0.1:3002").rstrip("/")


@pytest.fixture(autouse=True)
def _docker_playwright_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PLAYWRIGHT_USE_DOCKER", "1")
    yield
