from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
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
    AuditLogPage,
    AuditQueryParams,
    CreateActorRequest,
    CreateActorResponse,
    HealthStatus,
    ListActorsResponse,
    RevokeApiKeyResponse,
    VersionInfo,
    WhoamiResponse,
)
from aq_api.services.actors import create_actor as create_actor_service
from aq_api.services.actors import get_self_by_id
from aq_api.services.actors import list_actors as list_actor_service
from aq_api.services.api_keys import revoke_api_key as revoke_api_key_service
from aq_api.services.audit import query_audit_log as query_audit_log_service

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
