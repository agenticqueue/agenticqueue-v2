from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aq_api._datetime import parse_utc

ActorKind = Literal["human", "agent", "script", "routine"]


class AQModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def coerce_utc_datetime(value: object) -> datetime:
    if isinstance(value, str):
        return parse_utc(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return parse_utc(value.isoformat())
    raise ValueError("datetime value required")


def coerce_optional_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return coerce_utc_datetime(value)


class Actor(AQModel):
    id: UUID
    name: str = Field(min_length=1)
    kind: ActorKind
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


class ApiKey(AQModel):
    id: UUID
    actor_id: UUID
    name: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    created_at: datetime
    revoked_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("revoked_at", mode="before")
    @classmethod
    def revoked_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class SetupRequest(AQModel):
    pass


class SetupResponse(AQModel):
    actor_id: UUID
    founder_key: str = Field(min_length=1, repr=False)


class CreateActorRequest(AQModel):
    name: str = Field(min_length=1)
    kind: ActorKind
    key_name: str = Field(default="default", min_length=1)


class CreateActorResponse(AQModel):
    actor: Actor
    api_key: ApiKey
    key: str = Field(min_length=1, repr=False)


class RevokeApiKeyResponse(AQModel):
    api_key: ApiKey


class WhoamiResponse(AQModel):
    actor: Actor


class ListActorsResponse(AQModel):
    actors: list[Actor]
    next_cursor: str | None = None
