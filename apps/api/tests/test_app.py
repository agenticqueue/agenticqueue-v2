from datetime import UTC, datetime

from aq_api.app import app
from aq_api.models import HealthStatus, VersionInfo
from fastapi.testclient import TestClient

client = TestClient(app)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(UTC)


def test_healthz_returns_valid_health_status() -> None:
    before = datetime.now(UTC)
    response = client.get("/healthz")
    after = datetime.now(UTC)

    assert response.status_code == 200
    payload = response.json()
    status = HealthStatus.model_validate(payload)
    assert status.status == "ok"
    assert before <= _parse_utc(payload["timestamp"]) <= after


def test_version_returns_process_stable_version_info() -> None:
    first = client.get("/version")
    second = client.get("/version")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    VersionInfo.model_validate(first.json())


def test_openapi_documents_health_and_version() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["version"] == "0.1.0"
    paths = payload["paths"]
    assert paths["/healthz"]["get"]["responses"]["200"]
    assert paths["/version"]["get"]["responses"]["200"]
