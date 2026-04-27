from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)
from aq_api.models.labels import LabelName
from aq_api.models.projects import Cursor, Description

JobState = Literal[
    "draft",
    "ready",
    "in_progress",
    "done",
    "failed",
    "blocked",
    "pending_review",
    "cancelled",
]
JobEdgeType = Literal["gated_on", "parent_of", "sequence_next"]
JobTitle = Annotated[str, Field(min_length=1, max_length=512)]
PageLimit = Annotated[int, Field(default=50, ge=1, le=100)]


class Job(AQModel):
    id: UUID
    pipeline_id: UUID
    project_id: UUID
    state: JobState
    title: JobTitle
    description: Description = None
    contract_profile_id: UUID
    instantiated_from_step_id: UUID | None = None
    labels: list[LabelName] = Field(default_factory=list)
    claimed_by_actor_id: UUID | None = None
    claimed_at: datetime | None = None
    claim_heartbeat_at: datetime | None = None
    created_at: datetime
    created_by_actor_id: UUID | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)

    @field_validator("claimed_at", "claim_heartbeat_at", mode="before")
    @classmethod
    def claimed_times_must_be_utc(cls, value: object) -> datetime | None:
        return coerce_optional_utc_datetime(value)


class JobEdge(AQModel):
    from_job_id: UUID
    to_job_id: UUID
    edge_type: JobEdgeType


class CreateJobRequest(AQModel):
    pipeline_id: UUID
    title: JobTitle
    description: Description = None
    contract_profile_id: UUID


class CreateJobResponse(AQModel):
    job: Job


class ListJobsRequest(AQModel):
    project_id: UUID | None = None
    pipeline_id: UUID | None = None
    state: JobState | None = None
    limit: PageLimit = 50
    cursor: Cursor = None


class ListJobsResponse(AQModel):
    jobs: list[Job]
    next_cursor: Cursor = None


class GetJobResponse(AQModel):
    job: Job


class UpdateJobRequest(AQModel):
    title: JobTitle | None = None
    description: Description = None


class UpdateJobResponse(AQModel):
    job: Job


class ListReadyJobsRequest(AQModel):
    project_id: UUID
    label_filter: list[LabelName] = Field(default_factory=list)
    limit: PageLimit = 50
    cursor: Cursor = None


class ListReadyJobsResponse(AQModel):
    jobs: list[Job]
    next_cursor: Cursor = None


class CancelJobResponse(AQModel):
    job: Job
