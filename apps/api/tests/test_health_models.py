from datetime import UTC, datetime, timedelta, timezone

import pytest
from aq_api.models import HealthStatus, VersionInfo
from pydantic import ValidationError


def test_health_status_accepts_only_ok_with_utc_timestamp() -> None:
    status = HealthStatus(status="ok", timestamp=datetime(2026, 4, 26, tzinfo=UTC))

    assert status.status == "ok"
    assert status.timestamp == datetime(2026, 4, 26, tzinfo=UTC)

    with pytest.raises(ValidationError):
        HealthStatus(status="down", timestamp=datetime(2026, 4, 26, tzinfo=UTC))

    with pytest.raises(ValidationError):
        HealthStatus(status="ok", timestamp=datetime(2026, 4, 26))

    with pytest.raises(ValidationError):
        HealthStatus(
            status="ok",
            timestamp=datetime(2026, 4, 26, tzinfo=timezone(timedelta(hours=-5))),
        )


def test_version_info_validates_semver_commit_and_utc_built_at() -> None:
    version = VersionInfo(
        version="0.1.0",
        commit="da6e68f",
        built_at=datetime(2026, 4, 26, 15, 0, tzinfo=UTC),
    )

    assert version.version == "0.1.0"
    assert version.commit == "da6e68f"
    assert version.built_at == datetime(2026, 4, 26, 15, 0, tzinfo=UTC)

    with pytest.raises(ValidationError):
        VersionInfo(
            version="v0.1",
            commit="da6e68f",
            built_at=datetime(2026, 4, 26, 15, 0, tzinfo=UTC),
        )

    with pytest.raises(ValidationError):
        VersionInfo(
            version="0.1.0",
            commit="not-a-sha",
            built_at=datetime(2026, 4, 26, 15, 0, tzinfo=UTC),
        )

    with pytest.raises(ValidationError):
        VersionInfo(
            version="0.1.0",
            commit="da6e68f",
            built_at=datetime(2026, 4, 26, 15, 0),
        )


def test_contract_models_are_frozen_and_forbid_extra_fields() -> None:
    status = HealthStatus(status="ok", timestamp=datetime(2026, 4, 26, tzinfo=UTC))

    with pytest.raises(ValidationError):
        HealthStatus(
            status="ok",
            timestamp=datetime(2026, 4, 26, tzinfo=UTC),
            detail="extra",
        )

    with pytest.raises(ValidationError):
        status.status = "ok"
