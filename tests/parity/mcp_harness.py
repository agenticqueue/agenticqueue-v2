from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = (
    "http://127.0.0.1:8000" if Path("/.dockerenv").exists() else "http://localhost:8001"
)
TOOL_NAMES = [
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
    "list_pipelines",
    "get_pipeline",
    "update_pipeline",
    "clone_pipeline",
    "archive_pipeline",
    "create_job",
    "list_jobs",
    "get_job",
    "update_job",
    "list_ready_jobs",
    "claim_next_job",
    "comment_on_job",
    "list_job_comments",
    "cancel_job",
    "register_label",
    "attach_label",
    "detach_label",
]
DEFAULT_TOOL_ARGUMENTS = {
    "create_project": lambda: {
        "name": "MCP Harness Project",
        "slug": f"mcp-harness-{uuid.uuid4().hex[:12]}",
        "description": "Created by tests.parity.mcp_harness",
    }
}


def call_tool(
    tool_name: str,
    api_base_url: str,
    *,
    api_key: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,text/event-stream",
        "Content-Type": "application/json",
    }
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"

    response = httpx.post(
        f"{api_base_url.rstrip('/')}/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload["result"]
    if result.get("isError") is not False:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    structured_content = result["structuredContent"]
    if not isinstance(structured_content, dict):
        raise TypeError("MCP tool response did not include structuredContent")
    return structured_content


def _setup_key(api_base_url: str) -> str:
    response = httpx.post(f"{api_base_url.rstrip('/')}/setup", json={}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    return str(payload["founder_key"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Call an AQ2 MCP parity tool.")
    parser.add_argument("tool", choices=TOOL_NAMES)
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("AQ_API_URL", DEFAULT_API_URL),
        help="AQ API base URL; defaults to AQ_API_URL or localhost:8001.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AQ_API_KEY"),
        help="Bearer key; defaults to AQ_API_KEY.",
    )
    parser.add_argument(
        "--agent-identity",
        default=None,
        help="Optional informational MCP agent identity.",
    )
    parser.add_argument(
        "--arguments-json",
        default="{}",
        help="Tool arguments JSON object merged after --agent-identity.",
    )
    args = parser.parse_args()
    explicit_arguments = json.loads(args.arguments_json)
    if not isinstance(explicit_arguments, dict):
        raise TypeError("--arguments-json must decode to an object")
    default_args_factory = DEFAULT_TOOL_ARGUMENTS.get(args.tool)
    arguments = default_args_factory() if default_args_factory is not None else {}
    arguments.update(explicit_arguments)
    if args.agent_identity is not None:
        arguments["agent_identity"] = args.agent_identity
    api_key = args.api_key or _setup_key(args.api_base_url)
    print(
        json.dumps(
            call_tool(
                args.tool,
                args.api_base_url,
                api_key=api_key,
                arguments=arguments,
            ),
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
