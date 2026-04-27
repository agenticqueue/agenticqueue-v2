from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import AQModel, coerce_utc_datetime
from aq_api.models.projects import Cursor, Name, Slug

StepName = Annotated[str, Field(min_length=1, max_length=128)]
StepEdges = dict[str, object]


class WorkflowStepInput(AQModel):
    name: StepName
    ordinal: int = Field(ge=1)
    default_contract_profile_id: UUID
    step_edges: StepEdges = Field(default_factory=dict)


class WorkflowStep(AQModel):
    id: UUID
    workflow_id: UUID
    name: StepName
    ordinal: int = Field(ge=1)
    default_contract_profile_id: UUID
    step_edges: StepEdges = Field(default_factory=dict)


class Workflow(AQModel):
    id: UUID
    slug: Slug
    name: Name
    version: int = Field(ge=1)
    is_archived: bool = False
    created_at: datetime
    created_by_actor_id: UUID | None = None
    supersedes_workflow_id: UUID | None = None
    steps: list[WorkflowStep] = Field(default_factory=list)

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)


class CreateWorkflowRequest(AQModel):
    slug: Slug
    name: Name
    steps: list[WorkflowStepInput] = Field(min_length=1)


class CreateWorkflowResponse(AQModel):
    workflow: Workflow


class ListWorkflowsResponse(AQModel):
    workflows: list[Workflow]
    next_cursor: Cursor = None


class GetWorkflowResponse(AQModel):
    workflow: Workflow


class UpdateWorkflowRequest(AQModel):
    name: Name
    steps: list[WorkflowStepInput] = Field(min_length=1)


class UpdateWorkflowResponse(AQModel):
    workflow: Workflow


class ArchiveWorkflowResponse(AQModel):
    slug: Slug
    archived_count: int = Field(ge=0)
