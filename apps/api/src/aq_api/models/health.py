from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
GIT_SHORT_SHA_PATTERN = r"^[0-9a-f]{7,12}$"


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("datetime must be timezone-aware")
    if offset != timedelta(0):
        raise ValueError("datetime must be UTC")
    return value.astimezone(UTC)


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VersionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=SEMVER_PATTERN)
    commit: str = Field(pattern=GIT_SHORT_SHA_PATTERN)
    built_at: datetime

    @field_validator("built_at")
    @classmethod
    def built_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)
