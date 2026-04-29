import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.tests import run_server_async
from mcp.types import TextContent


@pytest.mark.asyncio
async def test_fastmcp_streamable_http_preserves_tool_result_content_blocks() -> None:
    server = FastMCP("multipart-spike", tasks=False)

    @server.tool(output_schema=None)
    async def multipart_spike() -> ToolResult:
        return ToolResult(
            content=[
                TextContent(type="text", text=json.dumps({"job": {"id": "job-1"}})),
                TextContent(
                    type="text",
                    text=json.dumps({"packet": {"current_job_id": "job-1"}}),
                ),
                TextContent(type="text", text="heartbeat every ~30s"),
            ],
            structured_content={"ok": True},
        )

    async with run_server_async(
        server,
        transport="streamable-http",
    ) as server_url:
        async with Client(StreamableHttpTransport(server_url)) as client:
            result = await client.call_tool("multipart_spike", {})

    assert result.structured_content == {"ok": True}
    assert result.is_error is False
    assert [block.model_dump(mode="json") for block in result.content] == [
        {
            "type": "text",
            "text": '{"job": {"id": "job-1"}}',
            "annotations": None,
            "meta": None,
        },
        {
            "type": "text",
            "text": '{"packet": {"current_job_id": "job-1"}}',
            "annotations": None,
            "meta": None,
        },
        {
            "type": "text",
            "text": "heartbeat every ~30s",
            "annotations": None,
            "meta": None,
        },
    ]
