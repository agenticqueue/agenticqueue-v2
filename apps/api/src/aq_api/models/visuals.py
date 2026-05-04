from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)

VisualAttachedToKind = Literal["project", "pipeline", "job", "decision", "learning"]
VisualType = Literal["mermaid", "graphviz", "plantuml", "vega-lite", "ascii"]
VisualSpec = Annotated[str, Field(min_length=1, max_length=65536)]
VisualCaption = Annotated[str | None, Field(default=None, max_length=512)]


class Visual(AQModel):
    id: UUID
    attached_to_kind: VisualAttachedToKind
    attached_to_id: UUID
    type: VisualType
    spec: VisualSpec
    caption: VisualCaption = None
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


class CreateVisualRequest(AQModel):
    attached_to_kind: VisualAttachedToKind
    attached_to_id: UUID
    type: VisualType
    spec: VisualSpec
    caption: VisualCaption = None


class UpdateVisualRequest(AQModel):
    spec: VisualSpec | None = None
    caption: VisualCaption = None
