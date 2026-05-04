import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import Field, TypeAdapter

from aq_api._health import current_health_status
from aq_api._request_context import (
    get_authenticated_actor_id,
    reset_claimed_actor_identity,
    set_claimed_actor_identity,
)
from aq_api._version import VERSION_INFO
from aq_api.models import (
    ActorKind,
    ArchivePipelineResponse,
    ArchiveProjectResponse,
    AttachLabelRequest,
    AttachLabelResponse,
    AuditLogPage,
    AuditQueryParams,
    CancelJobResponse,
    ClaimNextJobRequest,
    ClonePipelineRequest,
    ClonePipelineResponse,
    CommentOnJobRequest,
    CommentOnJobResponse,
    CreateActorRequest,
    CreateActorResponse,
    CreateComponentRequest,
    CreateComponentResponse,
    CreateDecisionRequest,
    CreateDecisionResponse,
    CreateJobRequest,
    CreateJobResponse,
    CreateObjectiveRequest,
    CreateObjectiveResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    EditLearningRequest,
    EditLearningResponse,
    GetComponentResponse,
    GetDecisionResponse,
    GetJobResponse,
    GetLearningResponse,
    GetObjectiveResponse,
    GetPipelineResponse,
    GetProjectResponse,
    HealthStatus,
    HeartbeatJobResponse,
    ListActorsResponse,
    ListComponentsResponse,
    ListDecisionsResponse,
    ListJobCommentsResponse,
    ListJobsResponse,
    ListLearningsResponse,
    ListObjectivesResponse,
    ListPipelinesResponse,
    ListProjectsResponse,
    ListReadyJobsResponse,
    RegisterLabelRequest,
    RegisterLabelResponse,
    ReleaseJobResponse,
    ResetClaimRequest,
    ResetClaimResponse,
    ReviewCompleteRequest,
    ReviewCompleteResponse,
    RevokeApiKeyResponse,
    SubmitJobRequest,
    SubmitLearningRequest,
    SubmitLearningResponse,
    SupersedeDecisionRequest,
    SupersedeDecisionResponse,
    UpdateComponentRequest,
    UpdateComponentResponse,
    UpdateJobResponse,
    UpdateObjectiveRequest,
    UpdateObjectiveResponse,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
    VersionInfo,
    WhoamiResponse,
)
from aq_api.models.components import (
    ComponentAccessPath,
    ComponentAttachedToKind,
    ComponentName,
    ComponentPurpose,
)
from aq_api.models.decisions import (
    AttachedToKind,
    DecisionRationale,
    DecisionStatement,
    DecisionTitle,
)
from aq_api.models.job_comments import CommentBody
from aq_api.models.jobs import JobState, JobTitle
from aq_api.models.labels import LabelColor, LabelName
from aq_api.models.learnings import LearningContext, LearningStatement, LearningTitle
from aq_api.models.objectives import (
    ObjectiveAttachedToKind,
    ObjectiveMetric,
    ObjectiveStatement,
    ObjectiveTargetValue,
)
from aq_api.models.pipelines import PipelineName
from aq_api.models.projects import (
    Description as ProjectDescription,
)
from aq_api.models.projects import (
    Name as ProjectName,
)
from aq_api.models.projects import (
    Slug as ProjectSlug,
)
from aq_api.services.actors import create_actor as create_actor_service
from aq_api.services.actors import get_self_by_id
from aq_api.services.actors import list_actors as list_actor_service
from aq_api.services.api_keys import revoke_api_key as revoke_api_key_service
from aq_api.services.audit import query_audit_log as query_audit_log_service
from aq_api.services.claim import claim_next_job as claim_next_job_service
from aq_api.services.components import create_component as create_component_service
from aq_api.services.components import get_component as get_component_service
from aq_api.services.components import list_components as list_component_service
from aq_api.services.components import update_component as update_component_service
from aq_api.services.decisions import create_decision as create_decision_service
from aq_api.services.decisions import get_decision as get_decision_service
from aq_api.services.decisions import list_decisions as list_decision_service
from aq_api.services.decisions import (
    supersede_decision as supersede_decision_service,
)
from aq_api.services.heartbeat import heartbeat_job as heartbeat_job_service
from aq_api.services.job_comments import comment_on_job as comment_on_job_service
from aq_api.services.job_comments import list_job_comments as list_comments_service
from aq_api.services.job_lifecycle import cancel_job as cancel_job_service
from aq_api.services.jobs import create_job as create_job_service
from aq_api.services.jobs import get_job as get_job_service
from aq_api.services.jobs import list_jobs as list_job_service
from aq_api.services.jobs import update_job as update_job_service
from aq_api.services.labels import attach_label as attach_label_service
from aq_api.services.labels import detach_label as detach_label_service
from aq_api.services.labels import register_label as register_label_service
from aq_api.services.learnings import edit_learning as edit_learning_service
from aq_api.services.learnings import get_learning as get_learning_service
from aq_api.services.learnings import list_learnings as list_learning_service
from aq_api.services.learnings import submit_learning as submit_learning_service
from aq_api.services.list_ready_jobs import list_ready_jobs as list_ready_jobs_service
from aq_api.services.objectives import create_objective as create_objective_service
from aq_api.services.objectives import get_objective as get_objective_service
from aq_api.services.objectives import list_objectives as list_objective_service
from aq_api.services.objectives import update_objective as update_objective_service
from aq_api.services.pipelines import archive_pipeline as archive_pipeline_service
from aq_api.services.pipelines import clone_pipeline as clone_pipeline_service
from aq_api.services.pipelines import create_pipeline as create_pipeline_service
from aq_api.services.pipelines import get_pipeline as get_pipeline_service
from aq_api.services.pipelines import list_pipelines as list_pipeline_service
from aq_api.services.pipelines import update_pipeline as update_pipeline_service
from aq_api.services.projects import archive_project as archive_project_service
from aq_api.services.projects import create_project as create_project_service
from aq_api.services.projects import get_project as get_project_service
from aq_api.services.projects import list_projects as list_project_service
from aq_api.services.projects import update_project as update_project_service
from aq_api.services.release import release_job as release_job_service
from aq_api.services.release import reset_claim as reset_claim_service
from aq_api.services.review import review_complete as review_complete_service
from aq_api.services.submit import submit_job as submit_job_service

MCP_NAME = "AgenticQueue 2.0 MCP"
MCP_HTTP_PATH = "/mcp"
MCP_INSTRUCTIONS = """You are connected to AgenticQueue 2.0's MCP server.

Conventions:
- Pass `agent_identity` (your API key alias) on every call. AQ does not infer it.
- Errors come back as structured objects: {error_code, rule_violated, details}.
  On `rule_violated`, do NOT retry — it indicates a fixable client mistake
  (wrong claimant, wrong state, missing field), not a transient failure.
- After a successful `claim_next_job`: the response includes a Context Packet
  (cap #8 forward-compat — currently a stub with empty `previous_jobs[]` and
  `next_job_id: null`). Read the Job's inline `contract` field for the DoD,
  call `heartbeat_job` every ~30 seconds while working, and submit finished
  work via `submit_job(job_id, payload)` with one of four outcomes:
  done | pending_review | failed | blocked. The payload's shape per outcome
  is described in the tool description. AQ validates the payload against the
  Job's inline `contract` field; mismatches return error_code=`contract_violation`
  with a `details` object naming the offending field.
- Resolve a `pending_review` Job via `review_complete(job_id, final_outcome)`.
  Any actor with a valid key can call this; the reviewing actor is recorded.
  final_outcome must be done or failed.
- `submit_job` accepts inline Decisions and Learnings via `decisions_made[]`
  and `learnings[]` arrays. Non-empty entries become rows attached to the
  submitting Job, returned as `created_decisions[]` and `created_learnings[]`
  in the response.
- Use `release_job` to return a claimed Job to `ready` only if you cannot
  complete it and want another worker to take it.
- Heartbeat cadence is recommended ~30 seconds. The server enforces only the
  AQ_CLAIM_LEASE_SECONDS lease (default 900s = 15 minutes); shorter cadence
  is friendlier to the auto-release sweep.
"""
AGENT_IDENTITY_PATTERN = r"^$|^[A-Za-z0-9_./:-]+$"
AgentIdentity = Annotated[
    str | None,
    Field(
        max_length=200,
        pattern=AGENT_IDENTITY_PATTERN,
        description=(
            "Informational caller identity recorded in audit rows; "
            "authentication remains the Bearer token."
        ),
    ),
]
SUBMIT_JOB_REQUEST_ADAPTER: TypeAdapter[SubmitJobRequest] = TypeAdapter(
    SubmitJobRequest
)


def _authenticated_actor_id() -> UUID:
    actor_id = get_authenticated_actor_id()
    if actor_id is None:
        raise RuntimeError("MCP tool requires authenticated Bearer context")
    return actor_id


def _json_block(payload: object) -> TextContent:
    return TextContent(
        type="text",
        text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )


@contextmanager
def _claimed_agent_identity(agent_identity: str | None) -> Iterator[None]:
    token = set_claimed_actor_identity(agent_identity or None)
    try:
        yield
    finally:
        reset_claimed_actor_identity(token)


def create_mcp_server() -> FastMCP:
    # No background task queue is needed for these synchronous read-only tools.
    server = FastMCP(MCP_NAME, tasks=False, instructions=MCP_INSTRUCTIONS)

    @server.tool(
        description=(
            "Return current health status with a per-call UTC timestamp. "
            "Read-only; safe to call repeatedly."
        ),
        annotations={"readOnlyHint": True},
    )
    async def health_check(agent_identity: AgentIdentity = None) -> HealthStatus:
        with _claimed_agent_identity(agent_identity):
            return current_health_status()

    @server.tool(
        description=(
            "Return the process-stable VersionInfo (version, commit, built_at). "
            "Read-only; result is byte-stable per process."
        ),
        annotations={"readOnlyHint": True},
    )
    async def get_version(agent_identity: AgentIdentity = None) -> VersionInfo:
        with _claimed_agent_identity(agent_identity):
            return VERSION_INFO

    @server.tool(
        description="Return the authenticated Actor for the caller's Bearer token.",
        annotations={"readOnlyHint": True},
    )
    async def get_self(agent_identity: AgentIdentity = None) -> WhoamiResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            async with SessionLocal() as session:
                return await get_self_by_id(session, _authenticated_actor_id())

    @server.tool(
        description=(
            "List active Actors with opaque cursor pagination. Read-only; "
            "deactivated Actors are excluded unless include_deactivated is true."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_actors(
        limit: int = 50,
        cursor: str | None = None,
        include_deactivated: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListActorsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_actor_service(
                    session,
                    limit=limit,
                    cursor=cursor,
                    include_deactivated=include_deactivated,
                )

    @server.tool(
        description=(
            "Create an Actor and mint its initial API key. Returns the plaintext "
            "key exactly once in this response."
        ),
        annotations={"readOnlyHint": False},
    )
    async def create_actor(
        name: str,
        kind: ActorKind,
        key_name: str = "default",
        agent_identity: AgentIdentity = None,
    ) -> CreateActorResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = CreateActorRequest(name=name, kind=kind, key_name=key_name)
            async with SessionLocal() as session:
                return await create_actor_service(session, request)

    @server.tool(
        description=(
            "Revoke one of the caller Actor's API keys. Cross-actor attempts "
            "are forbidden and audited; revoking the last active key is blocked."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def revoke_api_key(
        api_key_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> RevokeApiKeyResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            async with SessionLocal() as session:
                return await revoke_api_key_service(
                    session,
                    actor_id=actor_id,
                    api_key_id=api_key_id,
                )

    @server.tool(
        description=(
            "Query the append-only audit log with actor, operation, time-window, "
            "limit, and opaque cursor filters. Read-only and unaudited."
        ),
        annotations={"readOnlyHint": True},
    )
    async def query_audit_log(
        actor: str | None = None,
        op: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> AuditLogPage:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            params = AuditQueryParams.model_validate(
                {
                    "actor": actor,
                    "op": op,
                    "since": since,
                    "until": until,
                    "limit": limit,
                    "cursor": cursor,
                }
            )
            async with SessionLocal() as session:
                return await query_audit_log_service(session, params)

    @server.tool(
        description="Create a Project. Slug uniqueness is enforced globally.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_project(
        name: ProjectName,
        slug: ProjectSlug,
        description: ProjectDescription = None,
        agent_identity: AgentIdentity = None,
    ) -> CreateProjectResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateProjectRequest(
                name=name,
                slug=slug,
                description=description,
            )
            async with SessionLocal() as session:
                return await create_project_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Projects with opaque cursor pagination. Archived Projects are "
            "excluded unless include_archived is true."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_projects(
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListProjectsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_project_service(
                    session,
                    limit=limit,
                    cursor=cursor,
                    include_archived=include_archived,
                )

    @server.tool(
        description="Return one Project by UUID.",
        annotations={"readOnlyHint": True},
    )
    async def get_project(
        project_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetProjectResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_project_service(session, project_id)

    @server.tool(
        description="Update mutable Project metadata by UUID.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def update_project(
        project_id: UUID,
        name: ProjectName | None = None,
        description: ProjectDescription = None,
        agent_identity: AgentIdentity = None,
    ) -> UpdateProjectResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            data: dict[str, object] = {}
            if name is not None:
                data["name"] = name
            if description is not None:
                data["description"] = description
            request = UpdateProjectRequest.model_validate(data)
            async with SessionLocal() as session:
                return await update_project_service(session, project_id, request)

    @server.tool(
        description="Archive a Project by setting archived_at; rows are retained.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def archive_project(
        project_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> ArchiveProjectResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await archive_project_service(session, project_id)

    @server.tool(
        description=(
            "Create an ad-hoc Pipeline in a Project."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_pipeline(
        project_id: UUID,
        name: PipelineName,
        agent_identity: AgentIdentity = None,
    ) -> CreatePipelineResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreatePipelineRequest(project_id=project_id, name=name)
            async with SessionLocal() as session:
                return await create_pipeline_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description="List Pipelines with opaque cursor pagination.",
        annotations={"readOnlyHint": True},
    )
    async def list_pipelines(
        limit: int = 50,
        cursor: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ListPipelinesResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_pipeline_service(
                    session,
                    limit=limit,
                    cursor=cursor,
                )

    @server.tool(
        description="Return one Pipeline by UUID.",
        annotations={"readOnlyHint": True},
    )
    async def get_pipeline(
        pipeline_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetPipelineResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_pipeline_service(session, pipeline_id)

    @server.tool(
        description="Update mutable Pipeline metadata by UUID.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def update_pipeline(
        pipeline_id: UUID,
        name: PipelineName,
        agent_identity: AgentIdentity = None,
    ) -> UpdatePipelineResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = UpdatePipelineRequest(name=name)
            async with SessionLocal() as session:
                return await update_pipeline_service(
                    session,
                    pipeline_id,
                    request.model_dump(mode="json"),
                )

    @server.tool(
        description=(
            "Clone a Pipeline and copy its Jobs as ready Jobs with the same "
            "contracts, labels, titles, and descriptions."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def clone_pipeline(
        source_id: UUID,
        name: PipelineName,
        agent_identity: AgentIdentity = None,
    ) -> ClonePipelineResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = ClonePipelineRequest(name=name)
            async with SessionLocal() as session:
                return await clone_pipeline_service(
                    session,
                    source_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description="Archive a Pipeline by setting archived_at; Jobs are retained.",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def archive_pipeline(
        pipeline_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> ArchivePipelineResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await archive_pipeline_service(session, pipeline_id)

    @server.tool(
        description="Create a ready Job in a Pipeline with an inline Contract object.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_job(
        pipeline_id: UUID,
        title: JobTitle,
        contract: dict[str, object],
        description: ProjectDescription = None,
        agent_identity: AgentIdentity = None,
    ) -> CreateJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateJobRequest(
                pipeline_id=pipeline_id,
                title=title,
                description=description,
                contract=contract,
            )
            async with SessionLocal() as session:
                return await create_job_service(session, request, actor_id=actor_id)

    @server.tool(
        description=(
            "List Jobs with opaque cursor pagination and optional project_id, "
            "pipeline_id, and state filters."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_jobs(
        project_id: UUID | None = None,
        pipeline_id: UUID | None = None,
        state: JobState | None = None,
        limit: int = 50,
        cursor: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ListJobsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_job_service(
                    session,
                    project_id=project_id,
                    pipeline_id=pipeline_id,
                    state=state,
                    limit=limit,
                    cursor=cursor,
                )

    @server.tool(
        description="Return one Job by UUID.",
        annotations={"readOnlyHint": True},
    )
    async def get_job(
        job_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_job_service(session, job_id)

    @server.tool(
        description=(
            "Update mutable Job metadata. Only title and description are accepted; "
            "state, labels, claim metadata, and contract are rejected by the service."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def update_job(
        job_id: UUID,
        title: JobTitle | None = None,
        description: ProjectDescription = None,
        agent_identity: AgentIdentity = None,
    ) -> UpdateJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            data: dict[str, object] = {}
            if title is not None:
                data["title"] = title
            if description is not None:
                data["description"] = description
            async with SessionLocal() as session:
                return await update_job_service(session, job_id, data)

    @server.tool(
        description=(
            "List ready Jobs in a Project, optionally filtered by labels. "
            "Read-only and unaudited; template and archived Pipelines are excluded."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_ready_jobs(
        project_id: UUID,
        label_filter: list[LabelName] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ListReadyJobsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_ready_jobs_service(
                    session,
                    project_id=project_id,
                    label_filter=label_filter,
                    limit=limit,
                    cursor=cursor,
                )

    @server.tool(
        description=(
            "Atomically claim the next ready Job in a Project, optionally filtered "
            "by labels. Returns the Job, a stub Context Packet, and next-step text."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
        output_schema=None,
    )
    async def claim_next_job(
        project_id: UUID,
        label_filter: list[LabelName] | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ToolResult:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = ClaimNextJobRequest(
                project_id=project_id,
                label_filter=label_filter,
            )
            async with SessionLocal() as session:
                response = await claim_next_job_service(
                    session,
                    request=request,
                    actor_id=actor_id,
                )

        payload = response.model_dump(mode="json")
        packet_payload = response.packet.model_dump(mode="json")
        next_step = (
            f"You claimed Job {response.job.id} ({response.job.title}). "
            "Read the inline contract for the DoD; call heartbeat_job every "
            "~30s while working; call submit_job with done, pending_review, "
            "failed, or blocked when ready."
        )
        return ToolResult(
            content=[
                _json_block({"job": payload["job"]}),
                _json_block({"packet": packet_payload}),
                TextContent(type="text", text=next_step),
            ],
            structured_content=payload,
        )

    @server.tool(
        description=(
            "Submit a claimed Job with one of four outcomes: done, "
            "pending_review, failed, or blocked. done requires all contract DoDs "
            "to pass or be not_applicable; pending_review allows non-terminal "
            "DoD statuses with matching dod_ids; failed may omit dod_results but "
            "requires failure_reason; blocked requires gated_on_job_id and "
            "blocker_reason and writes a gated_on edge. All successful outcomes "
            "clear claim fields, record inline decisions_made/learnings, and "
            "write one audit row."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
        output_schema=None,
    )
    async def submit_job(
        job_id: UUID,
        payload: dict[str, object],
        agent_identity: AgentIdentity = None,
    ) -> ToolResult:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = SUBMIT_JOB_REQUEST_ADAPTER.validate_python(payload)
            async with SessionLocal() as session:
                response = await submit_job_service(
                    session,
                    job_id=job_id,
                    request=request,
                    actor_id=actor_id,
                )

        response_payload = response.model_dump(mode="json")
        next_step = (
            f"Job is now {response.job.state}. "
            "created_decisions and created_learnings list any inline D&L rows "
            "created with this submission."
        )
        return ToolResult(
            content=[
                _json_block({"job": response_payload["job"]}),
                TextContent(type="text", text=next_step),
            ],
            structured_content=response_payload,
        )

    @server.tool(
        description=(
            "Resolve a pending_review Job to a terminal state. Any actor with a "
            "valid key may call this; the reviewing actor is recorded in the "
            "audit log. final_outcome must be done or failed."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def review_complete(
        job_id: UUID,
        final_outcome: Literal["done", "failed"],
        notes: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ReviewCompleteResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = ReviewCompleteRequest(
                final_outcome=final_outcome,
                notes=notes,
            )
            async with SessionLocal() as session:
                return await review_complete_service(
                    session,
                    job_id=job_id,
                    request=request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Create a standalone Decision attached to a Job, Pipeline, or "
            "Project. Validates the attachment target and writes an audit row."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_decision(
        attached_to_kind: AttachedToKind,
        attached_to_id: UUID,
        title: DecisionTitle,
        statement: DecisionStatement,
        rationale: DecisionRationale = None,
        agent_identity: AgentIdentity = None,
    ) -> CreateDecisionResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateDecisionRequest(
                attached_to_kind=attached_to_kind,
                attached_to_id=attached_to_id,
                title=title,
                statement=statement,
                rationale=rationale,
            )
            async with SessionLocal() as session:
                return await create_decision_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Decisions with optional attachment, actor, since, cursor, "
            "limit, and include_deactivated filters. Read-only."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_decisions(
        attached_to_kind: AttachedToKind | None = None,
        attached_to_id: UUID | None = None,
        actor_id: UUID | None = None,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deactivated: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListDecisionsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_decision_service(
                    session,
                    attached_to_kind=attached_to_kind,
                    attached_to_id=attached_to_id,
                    actor_id=actor_id,
                    since=since,
                    cursor=cursor,
                    limit=limit,
                    include_deactivated=include_deactivated,
                )

    @server.tool(
        description=(
            "Get one Decision by id. Read-only. The visuals array is present "
            "but remains empty until Visual lookup wiring ships."
        ),
        annotations={"readOnlyHint": True},
    )
    async def get_decision(
        decision_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetDecisionResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_decision_service(session, decision_id)

    @server.tool(
        description=(
            "Supersede an active Decision with an active replacement Decision "
            "in the same attachment scope. Any valid Actor may call this."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def supersede_decision(
        decision_id: UUID,
        replacement_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> SupersedeDecisionResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = SupersedeDecisionRequest(replacement_id=replacement_id)
            async with SessionLocal() as session:
                return await supersede_decision_service(
                    session,
                    decision_id,
                    request,
                )

    @server.tool(
        description=(
            "Submit a standalone Learning attached to a Job, Pipeline, or "
            "Project. Validates the attachment target and writes an audit row."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def submit_learning(
        attached_to_kind: AttachedToKind,
        attached_to_id: UUID,
        title: LearningTitle,
        statement: LearningStatement,
        context: LearningContext = None,
        agent_identity: AgentIdentity = None,
    ) -> SubmitLearningResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = SubmitLearningRequest(
                attached_to_kind=attached_to_kind,
                attached_to_id=attached_to_id,
                title=title,
                statement=statement,
                context=context,
            )
            async with SessionLocal() as session:
                return await submit_learning_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Learnings with optional attachment, actor, since, cursor, "
            "limit, and include_deactivated filters. Read-only."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_learnings(
        attached_to_kind: AttachedToKind | None = None,
        attached_to_id: UUID | None = None,
        actor_id: UUID | None = None,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deactivated: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListLearningsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_learning_service(
                    session,
                    attached_to_kind=attached_to_kind,
                    attached_to_id=attached_to_id,
                    actor_id=actor_id,
                    since=since,
                    cursor=cursor,
                    limit=limit,
                    include_deactivated=include_deactivated,
                )

    @server.tool(
        description=(
            "Get one Learning by id. Read-only. The visuals array is present "
            "but remains empty until Visual lookup wiring ships."
        ),
        annotations={"readOnlyHint": True},
    )
    async def get_learning(
        learning_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetLearningResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_learning_service(session, learning_id)

    @server.tool(
        description=(
            "Edit a Learning's title, statement, or context. Creator-only; "
            "cross-actor edits return learning_edit_forbidden."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def edit_learning(
        learning_id: UUID,
        title: LearningTitle | None = None,
        statement: LearningStatement | None = None,
        context: LearningContext = None,
        agent_identity: AgentIdentity = None,
    ) -> EditLearningResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = EditLearningRequest(
                title=title,
                statement=statement,
                context=context,
            )
            async with SessionLocal() as session:
                return await edit_learning_service(
                    session,
                    learning_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Create an Objective attached to a Project or Pipeline. Validates "
            "the attachment target and writes an audit row."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_objective(
        attached_to_kind: ObjectiveAttachedToKind,
        attached_to_id: UUID,
        statement: ObjectiveStatement,
        metric: ObjectiveMetric = None,
        target_value: ObjectiveTargetValue = None,
        due_at: datetime | None = None,
        agent_identity: AgentIdentity = None,
    ) -> CreateObjectiveResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateObjectiveRequest(
                attached_to_kind=attached_to_kind,
                attached_to_id=attached_to_id,
                statement=statement,
                metric=metric,
                target_value=target_value,
                due_at=due_at,
            )
            async with SessionLocal() as session:
                return await create_objective_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Objectives with optional attachment, actor, since, cursor, "
            "limit, and include_deactivated filters. Read-only."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_objectives(
        attached_to_kind: ObjectiveAttachedToKind | None = None,
        attached_to_id: UUID | None = None,
        actor_id: UUID | None = None,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deactivated: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListObjectivesResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_objective_service(
                    session,
                    attached_to_kind=attached_to_kind,
                    attached_to_id=attached_to_id,
                    actor_id=actor_id,
                    since=since,
                    cursor=cursor,
                    limit=limit,
                    include_deactivated=include_deactivated,
                )

    @server.tool(
        description="Get one Objective by id. Read-only.",
        annotations={"readOnlyHint": True},
    )
    async def get_objective(
        objective_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetObjectiveResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_objective_service(session, objective_id)

    @server.tool(
        description=(
            "Update an Objective's statement, metric, target_value, or due_at. "
            "Creator-only; cross-actor updates return objective_update_forbidden."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def update_objective(
        objective_id: UUID,
        statement: ObjectiveStatement | None = None,
        metric: ObjectiveMetric = None,
        target_value: ObjectiveTargetValue = None,
        due_at: datetime | None = None,
        agent_identity: AgentIdentity = None,
    ) -> UpdateObjectiveResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = UpdateObjectiveRequest(
                statement=statement,
                metric=metric,
                target_value=target_value,
                due_at=due_at,
            )
            async with SessionLocal() as session:
                return await update_objective_service(
                    session,
                    objective_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Create a Component attached to a Project or Pipeline. Validates "
            "the attachment target and writes an audit row."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_component(
        attached_to_kind: ComponentAttachedToKind,
        attached_to_id: UUID,
        name: ComponentName,
        access_path: ComponentAccessPath,
        purpose: ComponentPurpose = None,
        agent_identity: AgentIdentity = None,
    ) -> CreateComponentResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateComponentRequest(
                attached_to_kind=attached_to_kind,
                attached_to_id=attached_to_id,
                name=name,
                purpose=purpose,
                access_path=access_path,
            )
            async with SessionLocal() as session:
                return await create_component_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Components with optional attachment, actor, since, cursor, "
            "limit, and include_deactivated filters. Read-only."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_components(
        attached_to_kind: ComponentAttachedToKind | None = None,
        attached_to_id: UUID | None = None,
        actor_id: UUID | None = None,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deactivated: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListComponentsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_component_service(
                    session,
                    attached_to_kind=attached_to_kind,
                    attached_to_id=attached_to_id,
                    actor_id=actor_id,
                    since=since,
                    cursor=cursor,
                    limit=limit,
                    include_deactivated=include_deactivated,
                )

    @server.tool(
        description="Get one Component by id. Read-only.",
        annotations={"readOnlyHint": True},
    )
    async def get_component(
        component_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetComponentResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_component_service(session, component_id)

    @server.tool(
        description=(
            "Update a Component's name, purpose, or access_path. Creator-only; "
            "cross-actor updates return component_update_forbidden."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def update_component(
        component_id: UUID,
        name: ComponentName | None = None,
        purpose: ComponentPurpose = None,
        access_path: ComponentAccessPath | None = None,
        agent_identity: AgentIdentity = None,
    ) -> UpdateComponentResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = UpdateComponentRequest(
                name=name,
                purpose=purpose,
                access_path=access_path,
            )
            async with SessionLocal() as session:
                return await update_component_service(
                    session,
                    component_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Add a durable Job comment. Audit rows record body_length only; "
            "the body is stored in job_comments."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def comment_on_job(
        job_id: UUID,
        body: CommentBody,
        agent_identity: AgentIdentity = None,
    ) -> CommentOnJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CommentOnJobRequest(body=body)
            async with SessionLocal() as session:
                return await comment_on_job_service(
                    session,
                    job_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Job comments in FIFO order with opaque cursor pagination. "
            "Read-only and unaudited."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_job_comments(
        job_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        agent_identity: AgentIdentity = None,
    ) -> ListJobCommentsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_comments_service(
                    session,
                    job_id,
                    limit=limit,
                    cursor=cursor,
                )

    @server.tool(
        description=(
            "Cancel a non-terminal Job. Terminal Jobs return already_terminal "
            "and remain unchanged."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def cancel_job(
        job_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> CancelJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await cancel_job_service(session, job_id)

    @server.tool(
        description=(
            "Release a Job claimed by the authenticated actor and return it "
            "to ready."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def release_job(
        job_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> ReleaseJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            async with SessionLocal() as session:
                return await release_job_service(
                    session,
                    job_id=job_id,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Reset a stuck claim with a required reason and return the Job "
            "to ready."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def reset_claim(
        job_id: UUID,
        reason: Annotated[str, Field(min_length=1)],
        agent_identity: AgentIdentity = None,
    ) -> ResetClaimResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = ResetClaimRequest(reason=reason)
            async with SessionLocal() as session:
                return await reset_claim_service(
                    session,
                    job_id=job_id,
                    request=request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "Refresh the claim heartbeat for a Job claimed by the authenticated "
            "actor. Successful heartbeats are lease maintenance and are not audited."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def heartbeat_job(
        job_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> HeartbeatJobResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            async with SessionLocal() as session:
                return await heartbeat_job_service(
                    session,
                    job_id=job_id,
                    actor_id=actor_id,
                )

    @server.tool(
        description="Register a Project-scoped Label for future Job attachment.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def register_label(
        project_id: UUID,
        name: LabelName,
        color: LabelColor = None,
        agent_identity: AgentIdentity = None,
    ) -> RegisterLabelResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = RegisterLabelRequest(name=name, color=color)
            async with SessionLocal() as session:
                return await register_label_service(session, project_id, request)

    @server.tool(
        description="Attach one registered Label to a Job's TEXT[] label cache.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def attach_label(
        job_id: UUID,
        label_name: LabelName,
        agent_identity: AgentIdentity = None,
    ) -> AttachLabelResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = AttachLabelRequest(label_name=label_name)
            async with SessionLocal() as session:
                return await attach_label_service(session, job_id, request)

    @server.tool(
        description="Detach one Label from a Job's TEXT[] label cache.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def detach_label(
        job_id: UUID,
        label_name: LabelName,
        agent_identity: AgentIdentity = None,
    ) -> DetachLabelResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            request = DetachLabelRequest(label_name=label_name)
            async with SessionLocal() as session:
                return await detach_label_service(session, job_id, request)

    return server


mcp = create_mcp_server()
mcp_http_app = mcp.http_app(
    # FastMCP owns the exact /mcp path; app.py extends it to avoid redirects.
    path=MCP_HTTP_PATH,
    transport="streamable-http",
    # Each HTTP MCP request stands alone; no session cookie is needed.
    stateless_http=True,
    # Return JSON instead of SSE; ADR-AQ-021 defers SSE to v1.1.
    json_response=True,
)


def stdio_main() -> None:
    mcp.run(transport="stdio", show_banner=False)
