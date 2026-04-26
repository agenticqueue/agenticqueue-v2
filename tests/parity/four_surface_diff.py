from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.parity.mcp_harness import call_tool  # noqa: E402

DEFAULT_API_URL = "http://localhost:8001"
DEFAULT_WEB_URL = "http://localhost:3002"
TEST_ID_RE = re.compile(r'data-testid="(?P<name>[^"]+)">(?P<value>[^<]+)')


def _get_json(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"{url} did not return a JSON object")
    return payload


def _run_cli(command: str, api_base_url: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["AQ_API_URL"] = api_base_url
    result = subprocess.run(
        ["uv", "run", "aq", command],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise TypeError("CLI did not return a JSON object")
    return payload


def _web_payloads(web_base_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    response = httpx.get(f"{web_base_url.rstrip('/')}/", timeout=10)
    response.raise_for_status()
    values = {
        match["name"]: match["value"] for match in TEST_ID_RE.finditer(response.text)
    }
    health = {
        "status": values["health-status"],
        "timestamp": values["health-timestamp"],
    }
    version = {
        "version": values["version-version"],
        "commit": values["version-commit"],
        "built_at": values["version-built-at"],
    }
    return health, version


def _normalize_health(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": payload["status"], "timestamp": "<per-call>"}


def _assert_equal(name: str, left: dict[str, Any], right: dict[str, Any]) -> None:
    if left != right:
        print(f"{name}=DIFF", file=sys.stderr)
        print(json.dumps({"left": left, "right": right}, indent=2), file=sys.stderr)
        raise SystemExit(1)
    print(f"{name}=OK")


def main() -> None:
    api_base_url = os.getenv("AQ_API_URL", DEFAULT_API_URL).rstrip("/")
    web_base_url = os.getenv("AQ_WEB_URL", DEFAULT_WEB_URL).rstrip("/")

    rest_health = _get_json(f"{api_base_url}/healthz")
    rest_version = _get_json(f"{api_base_url}/version")
    cli_health = _run_cli("health", api_base_url)
    cli_version = _run_cli("version", api_base_url)
    mcp_health = call_tool("health_check", api_base_url)
    mcp_version = call_tool("get_version", api_base_url)
    web_health, web_version = _web_payloads(web_base_url)

    _assert_equal(
        "REST_CLI_HEALTH",
        _normalize_health(rest_health),
        _normalize_health(cli_health),
    )
    _assert_equal("REST_CLI_VERSION", rest_version, cli_version)
    _assert_equal(
        "REST_MCP_HEALTH",
        _normalize_health(rest_health),
        _normalize_health(mcp_health),
    )
    _assert_equal("REST_MCP_VERSION", rest_version, mcp_version)
    _assert_equal(
        "REST_WEB_HEALTH",
        _normalize_health(rest_health),
        _normalize_health(web_health),
    )
    _assert_equal("REST_WEB_VERSION", rest_version, web_version)
    print("FOUR_SURFACE_EQUIVALENCE_OK")


if __name__ == "__main__":
    main()
