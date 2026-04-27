from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)


class AuditLogEntry(AQModel):
    id: UUID
    ts: datetime
    op: str = Field(min_length=1)
    authenticated_actor_id: UUID
    claimed_actor_identity: str | None = None
    target_kind: str | None = None
    target_id: UUID | None = None
    request_payload: dict[str, object] = Field(default_factory=dict)
    response_payload: dict[str, object] | None = None
    error_code: str | None = None

    @field_validator("ts", mode="before")
    @classmethod
    def ts_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)


class AuditQueryParams(AQModel):
    actor: UUID | None = None
    op: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None

    @field_validator("since", "until", mode="before")
    @classmethod
    def window_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class AuditLogPage(AQModel):
    entries: list[AuditLogEntry]
    next_cursor: str | None = None
