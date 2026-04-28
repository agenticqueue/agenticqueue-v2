from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from aq_api._health import current_health_status
from aq_api._request_context import (
    get_authenticated_actor_id,
    reset_claimed_actor_identity,
    set_claimed_actor_identity,
)
from aq_api._version import VERSION_INFO
from aq_api.models import (
    ActorKind,
    ArchiveProjectResponse,
    ArchiveWorkflowResponse,
    AttachLabelRequest,
    AttachLabelResponse,
    AuditLogPage,
    AuditQueryParams,
    CreateActorRequest,
    CreateActorResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    GetPipelineResponse,
    GetProjectResponse,
    GetWorkflowResponse,
    HealthStatus,
    InstantiatePipelineRequest,
    ListActorsResponse,
    ListPipelinesResponse,
    ListProjectsResponse,
    ListWorkflowsResponse,
    RegisterLabelRequest,
    RegisterLabelResponse,
    RevokeApiKeyResponse,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
    UpdateWorkflowRequest,
    UpdateWorkflowResponse,
    VersionInfo,
    WhoamiResponse,
    WorkflowStepInput,
)
from aq_api.models.labels import LabelColor, LabelName
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
from aq_api.services.instantiate import (
    instantiate_pipeline as instantiate_pipeline_service,
)
from aq_api.services.labels import attach_label as attach_label_service
from aq_api.services.labels import detach_label as detach_label_service
from aq_api.services.labels import register_label as register_label_service
from aq_api.services.pipelines import create_pipeline as create_pipeline_service
from aq_api.services.pipelines import get_pipeline as get_pipeline_service
from aq_api.services.pipelines import list_pipelines as list_pipeline_service
from aq_api.services.pipelines import update_pipeline as update_pipeline_service
from aq_api.services.projects import archive_project as archive_project_service
from aq_api.services.projects import create_project as create_project_service
from aq_api.services.projects import get_project as get_project_service
from aq_api.services.projects import list_projects as list_project_service
from aq_api.services.projects import update_project as update_project_service
from aq_api.services.workflows import archive_workflow as archive_workflow_service
from aq_api.services.workflows import create_workflow as create_workflow_service
from aq_api.services.workflows import get_workflow as get_workflow_service
from aq_api.services.workflows import list_workflows as list_workflow_service
from aq_api.services.workflows import update_workflow as update_workflow_service

MCP_NAME = "AgenticQueue 2.0 MCP"
MCP_HTTP_PATH = "/mcp"
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


def _authenticated_actor_id() -> UUID:
    actor_id = get_authenticated_actor_id()
    if actor_id is None:
        raise RuntimeError("MCP tool requires authenticated Bearer context")
    return actor_id


@contextmanager
def _claimed_agent_identity(agent_identity: str | None) -> Iterator[None]:
    token = set_claimed_actor_identity(agent_identity or None)
    try:
        yield
    finally:
        reset_claimed_actor_identity(token)


def create_mcp_server() -> FastMCP:
    # No background task queue is needed for these synchronous read-only tools.
    server = FastMCP(MCP_NAME, tasks=False)

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
        description="Create Workflow v1 with an ordered set of step definitions.",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_workflow(
        name: ProjectName,
        slug: ProjectSlug,
        steps: list[WorkflowStepInput],
        agent_identity: AgentIdentity = None,
    ) -> CreateWorkflowResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = CreateWorkflowRequest(name=name, slug=slug, steps=steps)
            async with SessionLocal() as session:
                return await create_workflow_service(
                    session,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description=(
            "List Workflows with opaque cursor pagination. Archived Workflow "
            "versions are excluded unless include_archived is true."
        ),
        annotations={"readOnlyHint": True},
    )
    async def list_workflows(
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
        agent_identity: AgentIdentity = None,
    ) -> ListWorkflowsResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await list_workflow_service(
                    session,
                    limit=limit,
                    cursor=cursor,
                    include_archived=include_archived,
                )

    @server.tool(
        description="Return one Workflow version by UUID.",
        annotations={"readOnlyHint": True},
    )
    async def get_workflow(
        workflow_id: UUID,
        agent_identity: AgentIdentity = None,
    ) -> GetWorkflowResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await get_workflow_service(session, workflow_id)

    @server.tool(
        description=(
            "Create the next Workflow version from a latest Workflow UUID. "
            "The prior row and prior steps are retained unchanged."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def update_workflow(
        workflow_id: UUID,
        name: ProjectName,
        steps: list[WorkflowStepInput],
        agent_identity: AgentIdentity = None,
    ) -> UpdateWorkflowResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = UpdateWorkflowRequest(name=name, steps=steps)
            async with SessionLocal() as session:
                return await update_workflow_service(
                    session,
                    workflow_id,
                    request,
                    actor_id=actor_id,
                )

    @server.tool(
        description="Archive every version in a Workflow slug family.",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def archive_workflow(
        slug: ProjectSlug,
        agent_identity: AgentIdentity = None,
    ) -> ArchiveWorkflowResponse:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            _authenticated_actor_id()
            async with SessionLocal() as session:
                return await archive_workflow_service(session, slug)

    @server.tool(
        description=(
            "Create an ad-hoc Pipeline in a Project without a Workflow link."
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
        description=(
            "Instantiate a Pipeline snapshot from the latest non-archived "
            "Workflow slug family and create ready Jobs for each step."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def instantiate_pipeline(
        workflow_slug: ProjectSlug,
        project_id: UUID,
        pipeline_name: PipelineName,
        agent_identity: AgentIdentity = None,
    ) -> ToolResult:
        from aq_api._db import SessionLocal

        with _claimed_agent_identity(agent_identity):
            actor_id = _authenticated_actor_id()
            request = InstantiatePipelineRequest(
                project_id=project_id,
                pipeline_name=pipeline_name,
            )
            async with SessionLocal() as session:
                response = await instantiate_pipeline_service(
                    session,
                    workflow_slug,
                    request,
                    actor_id=actor_id,
                )
        message = (
            f"Instantiated Pipeline {response.pipeline.name} from Workflow "
            f"{workflow_slug} v{response.pipeline.instantiated_from_workflow_version}. "
            f"{len(response.jobs)} Jobs created in state='ready' and immediately "
            "claimable. Each Job's contract_profile_id was set from its source "
            "step's default profile. Required next: optionally attach_label for "
            "routing, or hand off to cap #4's claim_next_job."
        )
        return ToolResult(
            content=message,
            structured_content=response.model_dump(mode="json"),
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
