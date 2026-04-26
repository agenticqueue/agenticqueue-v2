from datetime import UTC, datetime

from fastmcp import FastMCP

from aq_api._version import VERSION_INFO
from aq_api.models import HealthStatus, VersionInfo

MCP_NAME = "AgenticQueue 2.0 MCP"
MCP_HTTP_PATH = "/mcp"


def create_mcp_server() -> FastMCP:
    server = FastMCP(MCP_NAME, tasks=False)

    @server.tool
    def health_check() -> HealthStatus:
        return HealthStatus(status="ok", timestamp=datetime.now(UTC))

    @server.tool
    def get_version() -> VersionInfo:
        return VERSION_INFO

    return server


mcp = create_mcp_server()
mcp_http_app = mcp.http_app(
    path=MCP_HTTP_PATH,
    transport="streamable-http",
    stateless_http=True,
    json_response=True,
)


def stdio_main() -> None:
    mcp.run(transport="stdio", show_banner=False)
