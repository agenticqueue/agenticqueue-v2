import json
from importlib.metadata import version
from uuid import UUID

import httpx
import pytest
from _submit_job_test_support import DB_SKIP, auth_headers, claimed_job
from _submit_job_test_support import async_client as async_client  # noqa: F401
from _submit_job_test_support import conn as conn  # noqa: F401
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import isolated_schema as isolated_schema  # noqa: F401
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.mcp import create_mcp_server
from fastmcp import Client, FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from psycopg import Connection

pytestmark = DB_SKIP

CAP4_MUTATION_TOOLS = {
    "claim_next_job",
    "release_job",
    "reset_claim",
    "heartbeat_job",
}

CAP5_MUTATION_TOOLS = {
    "submit_job",
    "review_complete",
}


def _done_payload() -> dict[str, object]:
    return {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": ["pytest -q apps/api/tests/test_mcp_richness_cap05.py"],
                "summary": "MCP richness tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "No docs touched",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_mcp_richness_cap05.py"],
        "verification_summary": "MCP submit response verified",
        "files_changed": ["apps/api/src/aq_api/mcp.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-81",
        "decisions_made": [],
        "learnings": [],
    }


async def _mcp_call(
    actor_id: UUID,
    tool: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    context_token = set_authenticated_actor_id(actor_id)
    try:
        async with Client(create_mcp_server()) as client:
            result = await client.call_tool(tool, arguments)
    finally:
        reset_authenticated_actor_id(context_token)

    assert result.structured_content is not None
    return {
        "structuredContent": result.structured_content,
        "content": [block.model_dump(mode="json") for block in result.content],
    }


@pytest.mark.asyncio
async def test_mcp_instructions_reference_cap05_submit_and_review_paths() -> None:
    async with Client(create_mcp_server()) as client:
        initialize = await client.initialize()

    assert initialize.instructions is not None
    instructions = initialize.instructions
    assert "submit_job(job_id, payload)" in instructions
    assert "done | pending_review | failed | blocked" in instructions
    assert "review_complete(job_id, final_outcome)" in instructions
    assert "decisions_made[]" in instructions
    assert "learnings[]" in instructions
    assert "contract_violation" in instructions
    assert "submit_job ships in cap #5" not in instructions


@pytest.mark.asyncio
async def test_mcp_cap05_and_cap04_mutation_annotations_remain_destructive() -> None:
    async with Client(create_mcp_server()) as client:
        tools = await client.list_tools()

    tool_by_name = {tool.name: tool for tool in tools}
    for tool_name in CAP4_MUTATION_TOOLS | CAP5_MUTATION_TOOLS:
        annotations = tool_by_name[tool_name].annotations
        assert annotations is not None
        assert annotations.destructiveHint is True
        assert annotations.readOnlyHint is False
        assert annotations.idempotentHint is False

    submit_description = tool_by_name["submit_job"].description or ""
    assert "done requires" in submit_description
    assert "pending_review" in submit_description
    assert "failed may omit dod_results" in submit_description
    assert "blocked requires gated_on_job_id" in submit_description
    assert "decisions_made" in submit_description
    assert "learnings" in submit_description


@pytest.mark.asyncio
async def test_fastmcp_version_pin_preserves_toolresult_multipart_shape() -> None:
    assert version("fastmcp") == "2.14.7"
    smoke = FastMCP("cap-5 FastMCP multipart smoke", tasks=False)

    @smoke.tool(output_schema=None)
    async def multipart_smoke() -> ToolResult:
        return ToolResult(
            content=[
                TextContent(type="text", text='{"kind":"json"}'),
                TextContent(type="text", text="human text"),
            ],
            structured_content={"ok": True},
        )

    async with Client(smoke) as client:
        result = await client.call_tool("multipart_smoke", {})

    assert result.structured_content == {"ok": True}
    assert [block.text for block in result.content] == [
        '{"kind":"json"}',
        "human text",
    ]


@pytest.mark.asyncio
async def test_submit_job_mcp_response_is_two_blocks(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key, _project_id, _pipeline_id, job_id = claimed_job(
        conn,
        title="mcp cap5 submit target",
    )

    result = await _mcp_call(
        actor_id,
        "submit_job",
        {
            "job_id": str(job_id),
            "payload": _done_payload(),
            "agent_identity": "mcp-richness-cap05",
        },
    )

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    assert structured["job"]["id"] == str(job_id)
    assert structured["job"]["state"] == "done"
    assert structured["created_decisions"] == []
    assert structured["created_learnings"] == []

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert json.loads(content[0]["text"]) == {"job": structured["job"]}
    assert "Job is now done" in content[1]["text"]
    assert "created_decisions" in content[1]["text"]


@pytest.mark.asyncio
async def test_mcp_http_submit_job_response_is_two_blocks(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(
        conn,
        title="mcp cap5 http submit target",
    )

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["state"] == "done"
    assert "audit_row_id" not in payload
