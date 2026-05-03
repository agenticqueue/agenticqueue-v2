from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import (
    AQModel,
    coerce_optional_utc_datetime,
    coerce_utc_datetime,
)
from aq_api.models.decisions import SubmitDecisionInline
from aq_api.models.inheritance import InheritanceReferenceLists
from aq_api.models.labels import LabelName
from aq_api.models.learnings import SubmitLearningInline
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
DodResultStatus = Literal["passed", "failed", "blocked", "not_applicable"]
EvidencePointer = Annotated[str, Field(min_length=1, max_length=4096)]
CommandRun = Annotated[str, Field(min_length=1, max_length=4096)]
VerificationSummary = Annotated[str, Field(min_length=1, max_length=16384)]
ChangedFile = Annotated[str, Field(min_length=1, max_length=4096)]
RiskOrDeviation = Annotated[str, Field(min_length=1, max_length=16384)]
Handoff = Annotated[str, Field(min_length=1, max_length=16384)]
FailureReason = Annotated[str, Field(min_length=1, max_length=16384)]
BlockerReason = Annotated[str, Field(min_length=1, max_length=16384)]
ReviewReason = Annotated[str, Field(min_length=1, max_length=16384)]
ReviewNotes = Annotated[str | None, Field(default=None, max_length=16384)]


class Job(AQModel):
    id: UUID
    pipeline_id: UUID
    project_id: UUID
    state: JobState
    title: JobTitle
    description: Description = None
    contract: dict[str, object]
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
    contract: dict[str, object]


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
    decisions: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )
    learnings: InheritanceReferenceLists = Field(
        default_factory=InheritanceReferenceLists
    )


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


class ContextPacketStub(AQModel):
    project_id: UUID
    pipeline_id: UUID
    current_job_id: UUID
    previous_jobs: list[UUID] = Field(default_factory=list)
    next_job_id: UUID | None = None


class ClaimNextJobRequest(AQModel):
    project_id: UUID
    label_filter: list[LabelName] | None = None


class ClaimNextJobResponse(AQModel):
    job: Job
    packet: ContextPacketStub
    lease_seconds: int = Field(ge=60, le=86400)
    lease_expires_at: datetime
    recommended_heartbeat_after_seconds: int = Field(default=30, ge=1)

    @field_validator("lease_expires_at", mode="before")
    @classmethod
    def lease_expires_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)


class ReleaseJobResponse(AQModel):
    job: Job


class ResetClaimRequest(AQModel):
    reason: str = Field(min_length=1)


class ResetClaimResponse(AQModel):
    job: Job


class HeartbeatJobResponse(AQModel):
    job: Job


class CancelJobResponse(AQModel):
    job: Job


class SubmitJobDodResult(AQModel):
    dod_id: str = Field(min_length=1, max_length=512)
    status: DodResultStatus
    evidence: list[EvidencePointer] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=16384)


class _SubmitJobBaseRequest(AQModel):
    files_changed: list[ChangedFile] = Field(default_factory=list)
    risks_or_deviations: list[RiskOrDeviation] = Field(default_factory=list)
    handoff: Handoff
    learnings: list[SubmitLearningInline] = Field(default_factory=list)
    decisions_made: list[SubmitDecisionInline] = Field(default_factory=list)


class _SubmitJobProofRequest(_SubmitJobBaseRequest):
    dod_results: list[SubmitJobDodResult]
    commands_run: list[CommandRun] = Field(default_factory=list)
    verification_summary: VerificationSummary


class SubmitJobDoneRequest(_SubmitJobProofRequest):
    outcome: Literal["done"]


class SubmitJobPendingReviewRequest(_SubmitJobProofRequest):
    outcome: Literal["pending_review"]
    submitted_for_review: ReviewReason


class SubmitJobFailedRequest(_SubmitJobProofRequest):
    outcome: Literal["failed"]
    failure_reason: FailureReason
    dod_results: list[SubmitJobDodResult] = Field(default_factory=list)
    commands_run: list[CommandRun] = Field(default_factory=list)
    verification_summary: str = Field(default="", max_length=16384)


class SubmitJobBlockedRequest(_SubmitJobBaseRequest):
    outcome: Literal["blocked"]
    gated_on_job_id: UUID
    blocker_reason: BlockerReason


type SubmitJobRequest = Annotated[
    SubmitJobDoneRequest
    | SubmitJobPendingReviewRequest
    | SubmitJobFailedRequest
    | SubmitJobBlockedRequest,
    Field(discriminator="outcome"),
]


class SubmitJobResponse(AQModel):
    job: Job
    created_decisions: list[UUID] = Field(default_factory=list)
    created_learnings: list[UUID] = Field(default_factory=list)
    created_gated_on_edge: bool = False


class ReviewCompleteRequest(AQModel):
    final_outcome: Literal["done", "failed"]
    notes: ReviewNotes = None


class ReviewCompleteResponse(AQModel):
    job: Job
