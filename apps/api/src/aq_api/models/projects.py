from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)
from aq_api.models.inheritance import InheritanceReferenceLists

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
Name = Annotated[str, Field(min_length=1, max_length=256)]
Description = Annotated[str | None, Field(default=None, max_length=16384)]
Cursor = Annotated[str | None, Field(default=None, min_length=1)]


class Project(AQModel):
    id: UUID
    name: Name
    slug: Slug
    description: Description = None
    archived_at: datetime | None = None
    created_at: datetime
    created_by_actor_id: UUID | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("archived_at", mode="before")
    @classmethod
    def archived_at_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class CreateProjectRequest(AQModel):
    name: Name
    slug: Slug
    description: Description = None


class CreateProjectResponse(AQModel):
    project: Project


class ListProjectsResponse(AQModel):
    projects: list[Project]
    next_cursor: Cursor = None


class GetProjectResponse(AQModel):
    project: Project
    decisions: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )
    learnings: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )
    objectives: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )
    components: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )


class UpdateProjectRequest(AQModel):
    name: Name | None = None
    description: Description = None


class UpdateProjectResponse(AQModel):
    project: Project


class ArchiveProjectResponse(AQModel):
    project: Project
