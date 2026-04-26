from __future__ import annotations

import copy
import difflib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from aq_api._datetime import parse_utc
from aq_api.models import HealthStatus, VersionInfo

OPENAPI_SNAPSHOT = Path("tests/parity/openapi.snapshot.json")
MCP_SCHEMA_SNAPSHOT = Path("tests/parity/mcp_schema.snapshot.json")
TIME_WINDOW = timedelta(seconds=5)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_snapshot(
    path: Path,
    actual: Any,
    update_snapshots: bool,
    *,
    expected_override: Any | None = None,
) -> None:
    if update_snapshots:
        path.write_text(_json_text(actual), encoding="utf-8")
        return

    expected = expected_override if expected_override is not None else _load_json(path)
    actual_text = _json_text(actual)
    expected_text = _json_text(expected)
    if actual_text == expected_text:
        return

    diff = "\n".join(
        difflib.unified_diff(
            expected_text.splitlines(),
            actual_text.splitlines(),
            fromfile=str(path),
            tofile="live",
            lineterm="",
        )
    )
    raise AssertionError(f"{path} does not match live surface:\n{diff}")


def _get_json(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _post_mcp(
    mcp_base_url: str, method: str, params: dict[str, Any], request_id: int
) -> dict[str, Any]:
    response = httpx.post(
        mcp_base_url,
        headers={
            "Accept": "application/json,text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _call_mcp_tool(
    mcp_base_url: str, tool_name: str, request_id: int
) -> dict[str, Any]:
    payload = _post_mcp(
        mcp_base_url,
        "tools/call",
        {"name": tool_name, "arguments": {}},
        request_id,
    )
    result = payload["result"]
    assert result["isError"] is False
    structured_content = result["structuredContent"]
    assert isinstance(structured_content, dict)
    return structured_content


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
    assert isinstance(payload, dict)
    return payload


def _pnpm_executable() -> str:
    executable = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if executable is None:
        raise FileNotFoundError("Could not find pnpm or pnpm.cmd on PATH")
    return executable


def _git_short_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _assert_commit_matches_head(version: dict[str, Any]) -> None:
    head = _git_short_sha()
    assert version["commit"] == head, (
        f"commit mismatch: {version['commit']} vs {head}; rebuild compose with "
        "AQ_GIT_COMMIT=$(git rev-parse --short HEAD)"
    )


def _assert_health_payload(payload: dict[str, Any], started_at: datetime) -> None:
    health = HealthStatus.model_validate(payload)
    assert health.status == "ok"
    timestamp = parse_utc(str(payload["timestamp"]))
    assert started_at <= timestamp <= started_at + TIME_WINDOW


def _assert_version_payload(payload: dict[str, Any]) -> None:
    VersionInfo.model_validate(payload)
    _assert_commit_matches_head(payload)


def _assert_version_equal(left: dict[str, Any], right: dict[str, Any]) -> None:
    for field in ("version", "commit", "built_at"):
        assert right[field] == left[field]


def test_openapi_snapshot_matches_live_api(
    api_base_url: str, update_snapshots: bool
) -> None:
    live = _get_json(f"{api_base_url}/openapi.json")

    if update_snapshots:
        _assert_snapshot(OPENAPI_SNAPSHOT, live, update_snapshots)
        return

    expected = _load_json(OPENAPI_SNAPSHOT)
    normalized_live = copy.deepcopy(live)
    normalized_live["info"]["version"] = expected["info"]["version"]
    _assert_snapshot(
        OPENAPI_SNAPSHOT,
        normalized_live,
        update_snapshots,
        expected_override=expected,
    )


def test_mcp_schema_snapshot_matches_live_tools(
    mcp_base_url: str, update_snapshots: bool
) -> None:
    live = _post_mcp(mcp_base_url, "tools/list", {}, 1)
    _assert_snapshot(MCP_SCHEMA_SNAPSHOT, live, update_snapshots)


def test_rest_and_cli_payloads_match(api_base_url: str) -> None:
    started_at = datetime.now(UTC)
    rest_health = _get_json(f"{api_base_url}/healthz")
    rest_version = _get_json(f"{api_base_url}/version")
    cli_health = _run_cli("health", api_base_url)
    cli_version = _run_cli("version", api_base_url)

    assert cli_health["status"] == rest_health["status"]
    _assert_health_payload(rest_health, started_at)
    _assert_health_payload(cli_health, started_at)
    _assert_version_payload(rest_version)
    _assert_version_payload(cli_version)
    _assert_version_equal(rest_version, cli_version)


def test_rest_and_mcp_payloads_match(api_base_url: str, mcp_base_url: str) -> None:
    started_at = datetime.now(UTC)
    rest_health = _get_json(f"{api_base_url}/healthz")
    rest_version = _get_json(f"{api_base_url}/version")
    mcp_health = _call_mcp_tool(mcp_base_url, "health_check", 2)
    mcp_version = _call_mcp_tool(mcp_base_url, "get_version", 3)

    assert mcp_health["status"] == rest_health["status"]
    _assert_health_payload(rest_health, started_at)
    _assert_health_payload(mcp_health, started_at)
    _assert_version_payload(rest_version)
    _assert_version_payload(mcp_version)
    _assert_version_equal(rest_version, mcp_version)


def test_web_and_rest_payloads_match_via_playwright(
    api_base_url: str, artifact_dir: Path
) -> None:
    env = os.environ.copy()
    env["AQ_API_URL"] = api_base_url
    env["PLAYWRIGHT_USE_DOCKER"] = "1"

    result = subprocess.run(
        [
            _pnpm_executable(),
            "--filter",
            "@agenticqueue/web",
            "exec",
            "playwright",
            "test",
            "e2e/health.spec.ts",
            "--reporter=json",
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    (artifact_dir / "web-playwright-json.txt").write_text(
        result.stdout, encoding="utf-8"
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["stats"]["expected"] == 1
    assert report["stats"]["unexpected"] == 0
    assert report["stats"]["flaky"] == 0
    assert report["suites"][0]["specs"][0]["tests"][0]["results"][0]["status"] == (
        "passed"
    )
