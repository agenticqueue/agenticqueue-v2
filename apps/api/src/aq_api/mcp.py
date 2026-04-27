from uuid import UUID

from fastmcp import FastMCP

from aq_api._health import current_health_status
from aq_api._request_context import get_authenticated_actor_id
from aq_api._version import VERSION_INFO
from aq_api.models import (
    ActorKind,
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

MCP_NAME = "AgenticQueue 2.0 MCP"
MCP_HTTP_PATH = "/mcp"


def _authenticated_actor_id() -> UUID:
    actor_id = get_authenticated_actor_id()
    if actor_id is None:
        raise RuntimeError("MCP tool requires authenticated Bearer context")
    return actor_id


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
    async def health_check() -> HealthStatus:
        return current_health_status()

    @server.tool(
        description=(
            "Return the process-stable VersionInfo (version, commit, built_at). "
            "Read-only; result is byte-stable per process."
        ),
        annotations={"readOnlyHint": True},
    )
    async def get_version() -> VersionInfo:
        return VERSION_INFO

    @server.tool(
        description="Return the authenticated Actor for the caller's Bearer token.",
        annotations={"readOnlyHint": True},
    )
    async def get_self() -> WhoamiResponse:
        from aq_api._db import SessionLocal

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
    ) -> ListActorsResponse:
        from aq_api._db import SessionLocal

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
    ) -> CreateActorResponse:
        from aq_api._db import SessionLocal

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
    async def revoke_api_key(api_key_id: UUID) -> RevokeApiKeyResponse:
        from aq_api._db import SessionLocal

        actor_id = _authenticated_actor_id()
        async with SessionLocal() as session:
            return await revoke_api_key_service(
                session,
                actor_id=actor_id,
                api_key_id=api_key_id,
            )

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
