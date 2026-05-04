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
            "query_audit_log",
            "create_project",
            "list_projects",
            "get_project",
            "update_project",
            "archive_project",
            "create_pipeline",
            "clone_pipeline",
            "archive_pipeline",
            "list_pipelines",
            "get_pipeline",
            "update_pipeline",
            "create_job",
            "list_jobs",
            "get_job",
            "update_job",
            "list_ready_jobs",
            "claim_next_job",
            "submit_job",
            "review_complete",
            "create_decision",
            "list_decisions",
            "get_decision",
            "supersede_decision",
            "submit_learning",
            "list_learnings",
            "get_learning",
            "edit_learning",
            "create_objective",
            "list_objectives",
            "get_objective",
            "update_objective",
            "create_component",
            "list_components",
            "get_component",
            "update_component",
            "comment_on_job",
            "list_job_comments",
            "cancel_job",
            "release_job",
            "reset_claim",
            "heartbeat_job",
            "register_label",
            "attach_label",
            "detach_label",
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
        assert tool_by_name["query_audit_log"].annotations is not None
        assert tool_by_name["query_audit_log"].annotations.readOnlyHint is True
        assert tool_by_name["create_project"].annotations is not None
        assert tool_by_name["create_project"].annotations.readOnlyHint is False
        assert tool_by_name["create_project"].annotations.destructiveHint is False
        assert tool_by_name["list_projects"].annotations is not None
        assert tool_by_name["list_projects"].annotations.readOnlyHint is True
        assert tool_by_name["get_project"].annotations is not None
        assert tool_by_name["get_project"].annotations.readOnlyHint is True
        assert tool_by_name["update_project"].annotations is not None
        assert tool_by_name["update_project"].annotations.readOnlyHint is False
        assert tool_by_name["update_project"].annotations.destructiveHint is False
        assert tool_by_name["archive_project"].annotations is not None
        assert tool_by_name["archive_project"].annotations.readOnlyHint is False
        assert tool_by_name["archive_project"].annotations.destructiveHint is False
        assert tool_by_name["create_pipeline"].annotations is not None
        assert tool_by_name["create_pipeline"].annotations.readOnlyHint is False
        assert tool_by_name["create_pipeline"].annotations.destructiveHint is False
        assert tool_by_name["clone_pipeline"].annotations is not None
        assert tool_by_name["clone_pipeline"].annotations.readOnlyHint is False
        assert tool_by_name["clone_pipeline"].annotations.destructiveHint is False
        assert tool_by_name["archive_pipeline"].annotations is not None
        assert tool_by_name["archive_pipeline"].annotations.readOnlyHint is False
        assert tool_by_name["archive_pipeline"].annotations.destructiveHint is True
        assert tool_by_name["list_pipelines"].annotations is not None
        assert tool_by_name["list_pipelines"].annotations.readOnlyHint is True
        assert tool_by_name["get_pipeline"].annotations is not None
        assert tool_by_name["get_pipeline"].annotations.readOnlyHint is True
        assert tool_by_name["update_pipeline"].annotations is not None
        assert tool_by_name["update_pipeline"].annotations.readOnlyHint is False
        assert tool_by_name["update_pipeline"].annotations.destructiveHint is False
        assert tool_by_name["create_job"].annotations is not None
        assert tool_by_name["create_job"].annotations.readOnlyHint is False
        assert tool_by_name["create_job"].annotations.destructiveHint is False
        assert tool_by_name["list_jobs"].annotations is not None
        assert tool_by_name["list_jobs"].annotations.readOnlyHint is True
        assert tool_by_name["get_job"].annotations is not None
        assert tool_by_name["get_job"].annotations.readOnlyHint is True
        assert tool_by_name["update_job"].annotations is not None
        assert tool_by_name["update_job"].annotations.readOnlyHint is False
        assert tool_by_name["update_job"].annotations.destructiveHint is False
        assert tool_by_name["list_ready_jobs"].annotations is not None
        assert tool_by_name["list_ready_jobs"].annotations.readOnlyHint is True
        assert tool_by_name["claim_next_job"].annotations is not None
        assert tool_by_name["claim_next_job"].annotations.readOnlyHint is False
        assert tool_by_name["claim_next_job"].annotations.destructiveHint is True
        assert tool_by_name["claim_next_job"].annotations.idempotentHint is False
        assert tool_by_name["submit_job"].annotations is not None
        assert tool_by_name["submit_job"].annotations.readOnlyHint is False
        assert tool_by_name["submit_job"].annotations.destructiveHint is True
        assert tool_by_name["submit_job"].annotations.idempotentHint is False
        assert tool_by_name["review_complete"].annotations is not None
        assert tool_by_name["review_complete"].annotations.readOnlyHint is False
        assert tool_by_name["review_complete"].annotations.destructiveHint is True
        assert tool_by_name["review_complete"].annotations.idempotentHint is False
        assert tool_by_name["create_decision"].annotations is not None
        assert tool_by_name["create_decision"].annotations.readOnlyHint is False
        assert tool_by_name["create_decision"].annotations.destructiveHint is False
        assert tool_by_name["list_decisions"].annotations is not None
        assert tool_by_name["list_decisions"].annotations.readOnlyHint is True
        assert tool_by_name["get_decision"].annotations is not None
        assert tool_by_name["get_decision"].annotations.readOnlyHint is True
        assert tool_by_name["supersede_decision"].annotations is not None
        assert tool_by_name["supersede_decision"].annotations.readOnlyHint is False
        assert tool_by_name["supersede_decision"].annotations.destructiveHint is True
        assert tool_by_name["supersede_decision"].annotations.idempotentHint is False
        assert tool_by_name["submit_learning"].annotations is not None
        assert tool_by_name["submit_learning"].annotations.readOnlyHint is False
        assert tool_by_name["submit_learning"].annotations.destructiveHint is False
        assert tool_by_name["list_learnings"].annotations is not None
        assert tool_by_name["list_learnings"].annotations.readOnlyHint is True
        assert tool_by_name["get_learning"].annotations is not None
        assert tool_by_name["get_learning"].annotations.readOnlyHint is True
        assert tool_by_name["edit_learning"].annotations is not None
        assert tool_by_name["edit_learning"].annotations.readOnlyHint is False
        assert tool_by_name["edit_learning"].annotations.destructiveHint is False
        assert tool_by_name["edit_learning"].annotations.idempotentHint is False
        assert tool_by_name["create_objective"].annotations is not None
        assert tool_by_name["create_objective"].annotations.readOnlyHint is False
        assert tool_by_name["create_objective"].annotations.destructiveHint is False
        assert tool_by_name["list_objectives"].annotations is not None
        assert tool_by_name["list_objectives"].annotations.readOnlyHint is True
        assert tool_by_name["get_objective"].annotations is not None
        assert tool_by_name["get_objective"].annotations.readOnlyHint is True
        assert tool_by_name["update_objective"].annotations is not None
        assert tool_by_name["update_objective"].annotations.readOnlyHint is False
        assert tool_by_name["update_objective"].annotations.destructiveHint is False
        assert tool_by_name["update_objective"].annotations.idempotentHint is False
        assert tool_by_name["create_component"].annotations is not None
        assert tool_by_name["create_component"].annotations.readOnlyHint is False
        assert tool_by_name["create_component"].annotations.destructiveHint is False
        assert tool_by_name["list_components"].annotations is not None
        assert tool_by_name["list_components"].annotations.readOnlyHint is True
        assert tool_by_name["get_component"].annotations is not None
        assert tool_by_name["get_component"].annotations.readOnlyHint is True
        assert tool_by_name["update_component"].annotations is not None
        assert tool_by_name["update_component"].annotations.readOnlyHint is False
        assert tool_by_name["update_component"].annotations.destructiveHint is False
        assert tool_by_name["update_component"].annotations.idempotentHint is False
        assert tool_by_name["comment_on_job"].annotations is not None
        assert tool_by_name["comment_on_job"].annotations.readOnlyHint is False
        assert tool_by_name["comment_on_job"].annotations.destructiveHint is False
        assert tool_by_name["list_job_comments"].annotations is not None
        assert tool_by_name["list_job_comments"].annotations.readOnlyHint is True
        assert tool_by_name["cancel_job"].annotations is not None
        assert tool_by_name["cancel_job"].annotations.readOnlyHint is False
        assert tool_by_name["cancel_job"].annotations.destructiveHint is True
        assert tool_by_name["release_job"].annotations is not None
        assert tool_by_name["release_job"].annotations.readOnlyHint is False
        assert tool_by_name["release_job"].annotations.destructiveHint is True
        assert tool_by_name["release_job"].annotations.idempotentHint is False
        assert tool_by_name["reset_claim"].annotations is not None
        assert tool_by_name["reset_claim"].annotations.readOnlyHint is False
        assert tool_by_name["reset_claim"].annotations.destructiveHint is True
        assert tool_by_name["reset_claim"].annotations.idempotentHint is False
        assert tool_by_name["heartbeat_job"].annotations is not None
        assert tool_by_name["heartbeat_job"].annotations.readOnlyHint is False
        assert tool_by_name["heartbeat_job"].annotations.destructiveHint is True
        assert tool_by_name["heartbeat_job"].annotations.idempotentHint is False
        assert tool_by_name["register_label"].annotations is not None
        assert tool_by_name["register_label"].annotations.readOnlyHint is False
        assert tool_by_name["register_label"].annotations.destructiveHint is False
        assert tool_by_name["attach_label"].annotations is not None
        assert tool_by_name["attach_label"].annotations.readOnlyHint is False
        assert tool_by_name["attach_label"].annotations.destructiveHint is False
        assert tool_by_name["detach_label"].annotations is not None
        assert tool_by_name["detach_label"].annotations.readOnlyHint is False
        assert tool_by_name["detach_label"].annotations.destructiveHint is False
        for tool in tool_by_name.values():
            agent_schema = tool.inputSchema["properties"]["agent_identity"]
            assert agent_schema["default"] is None
            assert agent_schema["anyOf"][0]["maxLength"] == 200
            assert agent_schema["anyOf"][0]["pattern"] == "^$|^[A-Za-z0-9_./:-]+$"

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
