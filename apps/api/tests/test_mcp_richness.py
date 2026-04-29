import json
import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    truncate_job_state,
)
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.mcp import create_mcp_server
from fastmcp import Client
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

CAP4_MUTATION_TOOLS = {
    "claim_next_job",
    "release_job",
    "reset_claim",
    "heartbeat_job",
}

READ_ONLY_TOOLS = {
    "health_check",
    "get_version",
    "get_self",
    "list_actors",
    "query_audit_log",
    "list_projects",
    "get_project",
    "list_pipelines",
    "get_pipeline",
    "list_jobs",
    "get_job",
    "list_ready_jobs",
    "list_job_comments",
}


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_after_test() -> AsyncIterator[None]:
    yield
    if not DATABASE_URL_SYNC:
        return
    from aq_api._db import engine

    await engine.dispose()


def _fixture_project(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, UUID, UUID]:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-mcp-richness")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    return actor_id, project_id, pipeline_id


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
async def test_mcp_initialize_instructions_and_tool_annotations() -> None:
    async with Client(create_mcp_server()) as client:
        initialize = await client.initialize()
        tools = await client.list_tools()

    assert initialize.instructions is not None
    assert "You are connected to AgenticQueue 2.0's MCP server." in (
        initialize.instructions
    )
    assert "Pass `agent_identity` (your API key alias) on every call." in (
        initialize.instructions
    )
    assert "call `heartbeat_job` every ~30 seconds while working" in (
        initialize.instructions
    )

    tool_by_name = {tool.name: tool for tool in tools}
    for tool_name in CAP4_MUTATION_TOOLS:
        annotations = tool_by_name[tool_name].annotations
        assert annotations is not None
        assert annotations.destructiveHint is True
        assert annotations.readOnlyHint is False
        assert annotations.idempotentHint is False

    for tool_name in READ_ONLY_TOOLS:
        annotations = tool_by_name[tool_name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True


@pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live MCP richness claim test",
)
@pytest.mark.asyncio
async def test_claim_next_job_mcp_returns_job_packet_and_next_step_text(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="mcp richness job",
    )

    result = await _mcp_call(
        actor_id,
        "claim_next_job",
        {
            "project_id": str(project_id),
            "agent_identity": "mcp-richness-test",
        },
    )

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    assert structured["job"]["id"] == str(job_id)
    assert structured["packet"]["current_job_id"] == str(job_id)
    assert structured["packet"]["previous_jobs"] == []
    assert structured["packet"]["next_job_id"] is None

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 3
    assert json.loads(content[0]["text"]) == {"job": structured["job"]}
    assert json.loads(content[1]["text"]) == {"packet": structured["packet"]}
    assert content[2]["text"] == (
        f"You claimed Job {job_id} (mcp richness job). Read the inline contract "
        "for the DoD; call heartbeat_job every ~30s; submit_job ships in "
        "cap #5."
    )
