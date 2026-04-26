from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx

DEFAULT_API_URL = "http://localhost:8001"


def call_tool(tool_name: str, api_base_url: str) -> dict[str, Any]:
    response = httpx.post(
        f"{api_base_url.rstrip('/')}/mcp",
        headers={
            "Accept": "application/json,text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Call an AQ2 MCP parity tool.")
    parser.add_argument("tool", choices=["health_check", "get_version"])
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("AQ_API_URL", DEFAULT_API_URL),
        help="AQ API base URL; defaults to AQ_API_URL or localhost:8001.",
    )
    args = parser.parse_args()
    print(json.dumps(call_tool(args.tool, args.api_base_url), separators=(",", ":")))


if __name__ == "__main__":
    main()
