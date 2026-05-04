from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)

ComponentAttachedToKind = Literal["project", "pipeline"]
ComponentName = Annotated[str, Field(min_length=1, max_length=256)]
ComponentPurpose = Annotated[str | None, Field(default=None, max_length=16384)]
ComponentAccessPath = Annotated[str, Field(min_length=1, max_length=1024)]


class Component(AQModel):
    id: UUID
    attached_to_kind: ComponentAttachedToKind
    attached_to_id: UUID
    name: ComponentName
    purpose: ComponentPurpose = None
    access_path: ComponentAccessPath
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


class CreateComponentRequest(AQModel):
    attached_to_kind: ComponentAttachedToKind
    attached_to_id: UUID
    name: ComponentName
    purpose: ComponentPurpose = None
    access_path: ComponentAccessPath


class UpdateComponentRequest(AQModel):
    name: ComponentName | None = None
    purpose: ComponentPurpose = None
    access_path: ComponentAccessPath | None = None


class CreateComponentResponse(AQModel):
    component: Component


class ListComponentsResponse(AQModel):
    items: list[Component]
    next_cursor: str | None = None


class GetComponentResponse(AQModel):
    component: Component


class UpdateComponentResponse(AQModel):
    component: Component
