from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)

LabelName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9:_-]{0,127}$"),
]
LabelColor = Annotated[str | None, Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")]


class Label(AQModel):
    id: UUID
    project_id: UUID
    name: LabelName
    color: LabelColor = None
    created_at: datetime
    archived_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("archived_at", mode="before")
    @classmethod
    def archived_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class RegisterLabelRequest(AQModel):
    name: LabelName
    color: LabelColor = None


class RegisterLabelResponse(AQModel):
    label: Label


class AttachLabelRequest(AQModel):
    label_name: LabelName


class AttachLabelResponse(AQModel):
    job_id: UUID
    labels: list[LabelName]


class DetachLabelRequest(AQModel):
    label_name: LabelName


class DetachLabelResponse(AQModel):
    job_id: UUID
    labels: list[LabelName]
