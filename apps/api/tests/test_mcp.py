from datetime import UTC, datetime

import pytest
from aq_api._datetime import parse_utc
from aq_api._version import VERSION_INFO
from aq_api.app import app
from aq_api.mcp import create_mcp_server
from aq_api.models import HealthStatus, VersionInfo
from fastmcp import Client


@pytest.mark.asyncio
async def test_mcp_tools_return_shared_contract_payloads() -> None:
    before = datetime.now(UTC)

    async with Client(create_mcp_server()) as client:
        tools = await client.list_tools()
        tool_by_name = {tool.name: tool for tool in tools}
        assert set(tool_by_name) == {
            "health_check",
            "get_version",
            "get_self",
            "list_actors",
            "create_actor",
            "revoke_api_key",
        }
        assert tool_by_name["health_check"].annotations is not None
        assert tool_by_name["health_check"].annotations.readOnlyHint is True
        assert tool_by_name["get_version"].annotations is not None
        assert tool_by_name["get_version"].annotations.readOnlyHint is True
        assert tool_by_name["get_self"].annotations is not None
        assert tool_by_name["get_self"].annotations.readOnlyHint is True
        assert tool_by_name["list_actors"].annotations is not None
        assert tool_by_name["list_actors"].annotations.readOnlyHint is True
        assert tool_by_name["create_actor"].annotations is not None
        assert tool_by_name["create_actor"].annotations.readOnlyHint is False
        assert tool_by_name["revoke_api_key"].annotations is not None
        assert tool_by_name["revoke_api_key"].annotations.readOnlyHint is False
        assert tool_by_name["revoke_api_key"].annotations.destructiveHint is True

        health = await client.call_tool("health_check", {})
        version = await client.call_tool("get_version", {})

    assert health.structured_content is not None
    assert version.structured_content is not None
    health_payload = HealthStatus.model_validate(health.structured_content)
    version_payload = VersionInfo.model_validate(version.structured_content)
    assert health_payload.status == "ok"
    assert parse_utc(health.structured_content["timestamp"]) >= before
    assert version_payload == VERSION_INFO


def test_streamable_http_mcp_mount_is_registered() -> None:
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)
