from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import AQModel, coerce_utc_datetime
from aq_api.models.projects import Cursor, Name

PipelineName = Annotated[str, Field(min_length=1, max_length=256)]


class Pipeline(AQModel):
    id: UUID
    project_id: UUID
    name: PipelineName
    instantiated_from_workflow_id: UUID | None = None
    instantiated_from_workflow_version: int | None = Field(default=None, ge=1)
    created_at: datetime
    created_by_actor_id: UUID | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)


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


class UpdatePipelineRequest(AQModel):
    name: PipelineName


class UpdatePipelineResponse(AQModel):
    pipeline: Pipeline


class InstantiatePipelineRequest(AQModel):
    project_id: UUID
    pipeline_name: Name


class InstantiatePipelineResponse(AQModel):
    pipeline: Pipeline
    jobs: list["Job"]


from aq_api.models.jobs import Job  # noqa: E402

InstantiatePipelineResponse.model_rebuild()
