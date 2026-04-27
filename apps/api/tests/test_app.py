from collections.abc import AsyncIterator
from datetime import UTC, datetime

from aq_api._auth import current_actor
from aq_api._datetime import parse_utc
from aq_api.app import app
from aq_api.models import HealthStatus, VersionInfo
from fastapi.testclient import TestClient

client = TestClient(app)


async def _allow_auth() -> AsyncIterator[object]:
    yield object()


def _install_auth_override() -> None:
    app.dependency_overrides[current_actor] = _allow_auth


def _clear_auth_override() -> None:
    app.dependency_overrides.pop(current_actor, None)


def test_parse_utc_accepts_z_suffix() -> None:
    assert parse_utc("2026-04-26T15:55:14.282174Z") == datetime(
        2026,
        4,
        26,
        15,
        55,
        14,
        282174,
        tzinfo=UTC,
    )


def test_healthz_returns_valid_health_status() -> None:
    before = datetime.now(UTC)
    response = client.get("/healthz")
    after = datetime.now(UTC)

    assert response.status_code == 200
    payload = response.json()
    status = HealthStatus.model_validate(payload)
    assert status.status == "ok"
    assert before <= parse_utc(payload["timestamp"]) <= after


def test_version_returns_process_stable_version_info() -> None:
    _install_auth_override()
    try:
        first = client.get("/version")
        second = client.get("/version")
    finally:
        _clear_auth_override()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    VersionInfo.model_validate(first.json())


def test_openapi_documents_health_and_version() -> None:
    _install_auth_override()
    response = client.get("/openapi.json")
    try:
        version_response = client.get("/version")
    finally:
        _clear_auth_override()

    assert response.status_code == 200
    assert version_response.status_code == 200
    payload = response.json()
    assert payload["info"]["version"] == version_response.json()["version"]
    paths = payload["paths"]
    assert paths["/healthz"]["get"]["responses"]["200"]
    assert paths["/version"]["get"]["responses"]["200"]
