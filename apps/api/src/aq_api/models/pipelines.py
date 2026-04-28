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
from aq_api.models.projects import Cursor

PipelineName = Annotated[str, Field(min_length=1, max_length=256)]


class Pipeline(AQModel):
    id: UUID
    project_id: UUID
    name: PipelineName
    is_template: bool
    cloned_from_pipeline_id: UUID | None = None
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


class CreatePipelineRequest(AQModel):
    project_id: UUID
    name: PipelineName


class CreatePipelineResponse(AQModel):
    pipeline: Pipeline


class ListPipelinesResponse(AQModel):
    pipelines: list[Pipeline]
    next_cursor: Cursor = None


class GetPipelineResponse(AQModel):
    pipeline: Pipeline
    decisions: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )
    learnings: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )


class UpdatePipelineRequest(AQModel):
    name: PipelineName


class UpdatePipelineResponse(AQModel):
    pipeline: Pipeline


class ClonePipelineRequest(AQModel):
    name: PipelineName


class ClonePipelineResponse(AQModel):
    pipeline: Pipeline
    jobs: list["Job"]


class ArchivePipelineResponse(AQModel):
    pipeline: Pipeline


from aq_api.models.jobs import Job  # noqa: E402

ClonePipelineResponse.model_rebuild()
