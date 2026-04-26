from datetime import UTC, datetime

import pytest
from aq_api._datetime import parse_utc
from aq_api.app import app
from aq_api.mcp import create_mcp_server
from aq_api.models import HealthStatus, VersionInfo
from fastapi.testclient import TestClient
from fastmcp import Client


@pytest.mark.asyncio
async def test_mcp_tools_return_shared_contract_payloads() -> None:
    before = datetime.now(UTC)

    async with Client(create_mcp_server()) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert names == {"health_check", "get_version"}

        health = await client.call_tool("health_check", {})
        version = await client.call_tool("get_version", {})

    assert health.structured_content is not None
    assert version.structured_content is not None
    health_payload = HealthStatus.model_validate(health.structured_content)
    version_payload = VersionInfo.model_validate(version.structured_content)
    assert health_payload.status == "ok"
    assert parse_utc(health.structured_content["timestamp"]) >= before
    assert version_payload == VersionInfo.model_validate(
        TestClient(app).get("/version").json()
    )


def test_streamable_http_mcp_mount_is_registered() -> None:
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)
