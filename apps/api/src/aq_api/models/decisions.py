from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)

AttachedToKind = Literal["job", "pipeline", "project"]
DecisionTitle = Annotated[str, Field(min_length=1, max_length=512)]
DecisionStatement = Annotated[str, Field(min_length=1, max_length=16384)]
DecisionRationale = Annotated[str | None, Field(default=None, max_length=16384)]


class SubmitDecisionInline(AQModel):
    title: DecisionTitle
    statement: DecisionStatement
    rationale: DecisionRationale = None


class Decision(AQModel):
    id: UUID
    attached_to_kind: AttachedToKind
    attached_to_id: UUID
    title: DecisionTitle
    statement: DecisionStatement
    rationale: DecisionRationale = None
    supersedes_decision_id: UUID | None = None
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
