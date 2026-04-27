from fastmcp import FastMCP

from aq_api._health import current_health_status
from aq_api._version import VERSION_INFO
from aq_api.models import HealthStatus, VersionInfo

MCP_NAME = "AgenticQueue 2.0 MCP"
MCP_HTTP_PATH = "/mcp"


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
