from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)

ObjectiveAttachedToKind = Literal["project", "pipeline"]
ObjectiveStatement = Annotated[str, Field(min_length=1, max_length=16384)]
ObjectiveMetric = Annotated[str | None, Field(default=None, max_length=512)]
ObjectiveTargetValue = Annotated[str | None, Field(default=None, max_length=512)]


class Objective(AQModel):
    id: UUID
    attached_to_kind: ObjectiveAttachedToKind
    attached_to_id: UUID
    statement: ObjectiveStatement
    metric: ObjectiveMetric = None
    target_value: ObjectiveTargetValue = None
    due_at: datetime | None = None
    created_by_actor_id: UUID
    created_at: datetime
    deactivated_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("due_at", "deactivated_at", mode="before")
    @classmethod
    def optional_times_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class CreateObjectiveRequest(AQModel):
    attached_to_kind: ObjectiveAttachedToKind
    attached_to_id: UUID
    statement: ObjectiveStatement
    metric: ObjectiveMetric = None
    target_value: ObjectiveTargetValue = None
    due_at: datetime | None = None

    @field_validator("due_at", mode="before")
    @classmethod
    def due_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class UpdateObjectiveRequest(AQModel):
    statement: ObjectiveStatement | None = None
    metric: ObjectiveMetric = None
    target_value: ObjectiveTargetValue = None
    due_at: datetime | None = None

    @field_validator("due_at", mode="before")
    @classmethod
    def due_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)
