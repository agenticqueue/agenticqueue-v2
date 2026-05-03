from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)
from aq_api.models.decisions import AttachedToKind

LearningTitle = Annotated[str, Field(min_length=1, max_length=512)]
LearningStatement = Annotated[str, Field(min_length=1, max_length=16384)]
LearningContext = Annotated[str | None, Field(default=None, max_length=16384)]


class SubmitLearningInline(AQModel):
    title: LearningTitle
    statement: LearningStatement
    context: LearningContext = None


class Learning(AQModel):
    id: UUID
    attached_to_kind: AttachedToKind
    attached_to_id: UUID
    title: LearningTitle
    statement: LearningStatement
    context: LearningContext = None
    created_by_actor_id: UUID
    created_at: datetime
    deactivated_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("deactivated_at", mode="before")
    @classmethod
    def deactivated_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)
