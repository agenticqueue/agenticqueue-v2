from __future__ import annotations

import copy
import difflib
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
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


def _get_json(
    url: str,
    *,
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key is not None else None
    response = httpx.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _post_mcp(
    mcp_base_url: str,
    method: str,
    params: dict[str, Any],
    request_id: int,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,text/event-stream",
        "Content-Type": "application/json",
    }
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"

    response = httpx.post(
        mcp_base_url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _call_mcp_tool(
    mcp_base_url: str,
    tool_name: str,
    request_id: int,
    *,
    api_key: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _call_mcp_tool_result(
        mcp_base_url,
        tool_name,
        request_id,
        api_key=api_key,
        arguments=arguments,
    )
    structured_content = result["structuredContent"]
    assert isinstance(structured_content, dict)
    return structured_content


def _call_mcp_tool_result(
    mcp_base_url: str,
    tool_name: str,
    request_id: int,
    *,
    api_key: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _post_mcp(
        mcp_base_url,
        "tools/call",
        {"name": tool_name, "arguments": arguments or {}},
        request_id,
        api_key=api_key,
    )
    result = payload["result"]
    assert result["isError"] is False
    assert isinstance(result["structuredContent"], dict)
    return result


def _run_cli(
    command: str | list[str],
    api_base_url: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["AQ_API_URL"] = api_base_url
    if api_key is not None:
        env["AQ_API_KEY"] = api_key
    command_args = [command] if isinstance(command, str) else command
    result = subprocess.run(
        ["uv", "run", "aq", *command_args],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _direct_conninfo() -> str:
    value = os.getenv("DATABASE_URL_SYNC")
    if not value:
        raise RuntimeError("DATABASE_URL_SYNC is required for label parity")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _insert_pipeline_job(project_id: str, actor_id: str, title: str) -> str:
    with psycopg.connect(_direct_conninfo(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipelines (project_id, name, created_by_actor_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (project_id, f"label-parity-{uuid.uuid4().hex[:12]}", actor_id),
            )
            pipeline_row = cursor.fetchone()
            assert pipeline_row is not None
            pipeline_id = pipeline_row[0]
            cursor.execute(
                """
                INSERT INTO jobs
                    (
                        pipeline_id,
                        project_id,
                        state,
                        title,
                        contract,
                        created_by_actor_id
                    )
                VALUES (%s, %s, 'ready', %s, %s, %s)
                RETURNING id
                """,
                (
                    pipeline_id,
                    project_id,
                    title,
                    json.dumps(
                        {
                            "contract_type": "parity",
                            "dod_items": [{"id": "label-parity"}],
                        },
                        separators=(",", ":"),
                    ),
                    actor_id,
                ),
            )
            job_row = cursor.fetchone()
    assert job_row is not None
    return str(job_row[0])


def _submit_done_payload(dod_id: str) -> dict[str, Any]:
    return {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": dod_id,
                "status": "passed",
                "evidence": ["pytest -q tests/parity/test_four_surface_parity.py"],
                "summary": "parity submit verified",
            }
        ],
        "commands_run": ["pytest -q tests/parity/test_four_surface_parity.py"],
        "verification_summary": "submit done parity passed",
        "files_changed": ["tests/parity/test_four_surface_parity.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-77",
        "decisions_made": [],
        "learnings": [],
    }


def _submit_pending_review_payload(dod_id: str) -> dict[str, Any]:
    return {
        "outcome": "pending_review",
        "submitted_for_review": "parity reviewer requested",
        "dod_results": [
            {
                "dod_id": dod_id,
                "status": "blocked",
                "evidence": [],
                "summary": "review still needed",
            }
        ],
        "commands_run": ["pytest -q tests/parity/test_four_surface_parity.py"],
        "verification_summary": "pending_review parity passed",
        "files_changed": ["tests/parity/test_four_surface_parity.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-78",
        "decisions_made": [],
        "learnings": [],
    }


def _submit_failed_payload() -> dict[str, Any]:
    return {
        "outcome": "failed",
        "failure_reason": "parity failure path exercised",
        "files_changed": ["tests/parity/test_four_surface_parity.py"],
        "risks_or_deviations": ["parity failure is synthetic"],
        "handoff": "AQ2-78",
        "decisions_made": [],
        "learnings": [],
    }


def _submit_blocked_payload(gated_on_job_id: str) -> dict[str, Any]:
    return {
        "outcome": "blocked",
        "gated_on_job_id": gated_on_job_id,
        "blocker_reason": "waiting for parity gating Job",
        "files_changed": ["tests/parity/test_four_surface_parity.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-78",
        "decisions_made": [],
        "learnings": [],
    }


def _review_complete_payload() -> dict[str, Any]:
    return {"final_outcome": "done", "notes": "parity review accepted"}


def _insert_pipeline_for_listing(
    project_id: str,
    actor_id: str,
    name: str,
    *,
    is_template: bool,
) -> str:
    with psycopg.connect(_direct_conninfo(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipelines
                    (project_id, name, is_template, created_by_actor_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (project_id, name, is_template, actor_id),
            )
            row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _create_pipeline_triplet(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    *,
    label: str,
    mcp_request_start: int,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    auth = {"Authorization": f"Bearer {founder_key}"}

    rest_project_response = httpx.post(
        f"{api_base_url}/projects",
        headers=auth,
        json={
            "name": f"REST {label} Project",
            "slug": f"parity-rest-{label}-{suffix}",
        },
        timeout=10,
    )
    rest_project_response.raise_for_status()
    rest_project = rest_project_response.json()

    cli_project = _run_cli(
        [
            "project",
            "create",
            "--name",
            f"CLI {label} Project",
            "--slug",
            f"parity-cli-{label}-{suffix}",
        ],
        api_base_url,
        api_key=founder_key,
    )

    mcp_project = _call_mcp_tool(
        mcp_base_url,
        "create_project",
        mcp_request_start,
        api_key=founder_key,
        arguments={
            "name": f"MCP {label} Project",
            "slug": f"parity-mcp-{label}-{suffix}",
            "agent_identity": f"parity-{label}",
        },
    )

    rest_create_response = httpx.post(
        f"{api_base_url}/pipelines",
        headers=auth,
        json={
            "project_id": rest_project["project"]["id"],
            "name": f"REST {label} Pipeline",
        },
        timeout=10,
    )
    rest_create_response.raise_for_status()
    rest_pipeline = rest_create_response.json()

    cli_pipeline = _run_cli(
        [
            "pipeline",
            "create",
            "--project",
            cli_project["project"]["id"],
            "--name",
            f"CLI {label} Pipeline",
        ],
        api_base_url,
        api_key=founder_key,
    )

    mcp_pipeline = _call_mcp_tool(
        mcp_base_url,
        "create_pipeline",
        mcp_request_start + 1,
        api_key=founder_key,
        arguments={
            "project_id": mcp_project["project"]["id"],
            "name": f"MCP {label} Pipeline",
            "agent_identity": f"parity-{label}",
        },
    )

    return {
        "projects": {
            "rest": rest_project,
            "cli": cli_project,
            "mcp": mcp_project,
        },
        "pipelines": {
            "rest": rest_pipeline,
            "cli": cli_pipeline,
            "mcp": mcp_pipeline,
        },
        "pipeline_ids": {
            "rest": rest_pipeline["pipeline"]["id"],
            "cli": cli_pipeline["pipeline"]["id"],
            "mcp": mcp_pipeline["pipeline"]["id"],
        },
    }


def _pnpm_executable() -> str:
    executable = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if executable is None:
        raise FileNotFoundError("Could not find pnpm or pnpm.cmd on PATH")
    return executable


def _git_short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return os.getenv("AQ_GIT_COMMIT", "0000000")[:7]


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
    mcp_base_url: str, founder_key: str, update_snapshots: bool
) -> None:
    live = _post_mcp(mcp_base_url, "tools/list", {}, 1, api_key=founder_key)
    _assert_snapshot(MCP_SCHEMA_SNAPSHOT, live, update_snapshots)


def test_rest_and_cli_payloads_match(api_base_url: str, founder_key: str) -> None:
    started_at = datetime.now(UTC)
    rest_health = _get_json(f"{api_base_url}/healthz")
    rest_version = _get_json(f"{api_base_url}/version", api_key=founder_key)
    cli_health = _run_cli("health", api_base_url)
    cli_version = _run_cli("version", api_base_url, api_key=founder_key)

    assert cli_health["status"] == rest_health["status"]
    _assert_health_payload(rest_health, started_at)
    _assert_health_payload(cli_health, started_at)
    _assert_version_payload(rest_version)
    _assert_version_payload(cli_version)
    _assert_version_equal(rest_version, cli_version)


def test_rest_and_mcp_payloads_match(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
) -> None:
    started_at = datetime.now(UTC)
    rest_health = _get_json(f"{api_base_url}/healthz")
    rest_version = _get_json(f"{api_base_url}/version", api_key=founder_key)
    mcp_health = _call_mcp_tool(
        mcp_base_url,
        "health_check",
        2,
        api_key=founder_key,
    )
    mcp_version = _call_mcp_tool(
        mcp_base_url,
        "get_version",
        3,
        api_key=founder_key,
    )

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
    try:
        pnpm = _pnpm_executable()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))

    result = subprocess.run(
        [
            pnpm,
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


def test_whoami_matches_rest_cli_mcp_and_reads_do_not_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    audit_params = {"actor": founder_actor_id}
    before_audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params=audit_params,
    )
    rest = _get_json(f"{api_base_url}/actors/me", api_key=founder_key)
    cli = _run_cli("whoami", api_base_url, api_key=founder_key)
    mcp = _call_mcp_tool(
        mcp_base_url,
        "get_self",
        10,
        api_key=founder_key,
        arguments={"agent_identity": "parity-whoami"},
    )
    actor_list = _get_json(
        f"{api_base_url}/actors",
        api_key=founder_key,
        params={"limit": 200},
    )
    after_audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params=audit_params,
    )

    assert before_audit == {"entries": [], "next_cursor": None}
    assert rest == cli == mcp
    assert founder_actor_id in {actor["id"] for actor in actor_list["actors"]}
    assert after_audit == before_audit

    artifact = {
        "rest": rest,
        "cli": cli,
        "mcp": mcp,
        "audit_before": before_audit,
        "audit_after": after_audit,
    }
    (artifact_dir / "whoami-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_audit_query_matches_rest_cli_mcp_and_injection_returns_zero_rows(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    create_payload = {
        "name": f"parity-test-created-actor-{uuid.uuid4().hex[:12]}",
        "kind": "agent",
    }
    create_response = httpx.post(
        f"{api_base_url}/actors",
        headers={"Authorization": f"Bearer {founder_key}"},
        json=create_payload,
        timeout=10,
    )
    create_response.raise_for_status()

    params = {"actor": founder_actor_id, "op": "create_actor", "limit": 20}
    rest = _get_json(f"{api_base_url}/audit", api_key=founder_key, params=params)
    cli = _run_cli(
        [
            "audit",
            "--actor",
            founder_actor_id,
            "--op",
            "create_actor",
            "--limit",
            "20",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp = _call_mcp_tool(
        mcp_base_url,
        "query_audit_log",
        11,
        api_key=founder_key,
        arguments={
            "op": "create_actor",
            "actor": founder_actor_id,
            "limit": 20,
            "agent_identity": "parity-audit",
        },
    )

    injection = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"op": "create_actor' OR '1'='1", "limit": 20},
    )

    assert rest == cli == mcp
    assert len(rest["entries"]) == 1
    assert rest["entries"][0]["op"] == "create_actor"
    assert rest["entries"][0]["error_code"] is None
    assert injection == {"entries": [], "next_cursor": None}

    artifact = {"rest": rest, "cli": cli, "mcp": mcp, "injection": injection}
    (artifact_dir / "audit-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_project_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    auth = {"Authorization": f"Bearer {founder_key}"}
    rest_payload = {
        "name": "REST Project",
        "slug": f"parity-rest-project-{suffix}",
        "description": "Created through REST",
    }
    cli_args = [
        "project",
        "create",
        "--name",
        "CLI Project",
        "--slug",
        f"parity-cli-project-{suffix}",
        "--description",
        "Created through CLI",
    ]
    mcp_args = {
        "name": "MCP Project",
        "slug": f"parity-mcp-project-{suffix}",
        "description": "Created through MCP",
        "agent_identity": "parity-project",
    }

    rest_create_response = httpx.post(
        f"{api_base_url}/projects",
        headers=auth,
        json=rest_payload,
        timeout=10,
    )
    rest_create_response.raise_for_status()
    rest_create = rest_create_response.json()
    cli_create = _run_cli(cli_args, api_base_url, api_key=founder_key)
    mcp_create = _call_mcp_tool(
        mcp_base_url,
        "create_project",
        20,
        api_key=founder_key,
        arguments=mcp_args,
    )

    rest_project_id = rest_create["project"]["id"]
    cli_project_id = cli_create["project"]["id"]
    mcp_project_id = mcp_create["project"]["id"]

    rest_list = _get_json(
        f"{api_base_url}/projects",
        api_key=founder_key,
        params={"limit": 200},
    )
    cli_list = _run_cli(
        ["project", "list", "--limit", "200"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_projects",
        21,
        api_key=founder_key,
        arguments={"limit": 200, "agent_identity": "parity-project"},
    )
    expected_ids = {rest_project_id, cli_project_id, mcp_project_id}
    for page in (rest_list, cli_list, mcp_list):
        assert expected_ids.issubset({project["id"] for project in page["projects"]})

    rest_get = _get_json(
        f"{api_base_url}/projects/{rest_project_id}",
        api_key=founder_key,
    )
    cli_get = _run_cli(
        ["project", "get", cli_project_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_get = _call_mcp_tool(
        mcp_base_url,
        "get_project",
        22,
        api_key=founder_key,
        arguments={
            "project_id": mcp_project_id,
            "agent_identity": "parity-project",
        },
    )
    assert rest_get["project"]["id"] == rest_project_id
    assert cli_get["project"]["id"] == cli_project_id
    assert mcp_get["project"]["id"] == mcp_project_id

    rest_update_response = httpx.patch(
        f"{api_base_url}/projects/{rest_project_id}",
        headers=auth,
        json={"description": "REST updated"},
        timeout=10,
    )
    rest_update_response.raise_for_status()
    rest_update = rest_update_response.json()
    cli_update = _run_cli(
        [
            "project",
            "update",
            cli_project_id,
            "--description",
            "CLI updated",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_update = _call_mcp_tool(
        mcp_base_url,
        "update_project",
        23,
        api_key=founder_key,
        arguments={
            "project_id": mcp_project_id,
            "description": "MCP updated",
            "agent_identity": "parity-project",
        },
    )
    assert rest_update["project"]["description"] == "REST updated"
    assert cli_update["project"]["description"] == "CLI updated"
    assert mcp_update["project"]["description"] == "MCP updated"

    rest_archive_response = httpx.post(
        f"{api_base_url}/projects/{rest_project_id}/archive",
        headers=auth,
        json={},
        timeout=10,
    )
    rest_archive_response.raise_for_status()
    rest_archive = rest_archive_response.json()
    cli_archive = _run_cli(
        ["project", "archive", cli_project_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_archive = _call_mcp_tool(
        mcp_base_url,
        "archive_project",
        24,
        api_key=founder_key,
        arguments={
            "project_id": mcp_project_id,
            "agent_identity": "parity-project",
        },
    )
    assert rest_archive["project"]["archived_at"] is not None
    assert cli_archive["project"]["archived_at"] is not None
    assert mcp_archive["project"]["archived_at"] is not None

    rest_after_archive = _get_json(
        f"{api_base_url}/projects",
        api_key=founder_key,
        params={"limit": 200},
    )
    cli_after_archive = _run_cli(
        ["project", "list", "--limit", "200"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_after_archive = _call_mcp_tool(
        mcp_base_url,
        "list_projects",
        25,
        api_key=founder_key,
        arguments={"limit": 200, "agent_identity": "parity-project"},
    )
    for page in (rest_after_archive, cli_after_archive, mcp_after_archive):
        assert expected_ids.isdisjoint({project["id"] for project in page["projects"]})

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 20},
    )
    project_ops = [
        row["op"]
        for row in audit["entries"]
        if row["op"] in {"create_project", "update_project", "archive_project"}
    ]
    assert project_ops.count("create_project") == 3
    assert project_ops.count("update_project") == 3
    assert project_ops.count("archive_project") == 3

    artifact = {
        "creates": {"rest": rest_create, "cli": cli_create, "mcp": mcp_create},
        "lists": {
            "rest": rest_list,
            "cli": cli_list,
            "mcp": mcp_list,
            "rest_after_archive": rest_after_archive,
            "cli_after_archive": cli_after_archive,
            "mcp_after_archive": mcp_after_archive,
        },
        "gets": {"rest": rest_get, "cli": cli_get, "mcp": mcp_get},
        "updates": {"rest": rest_update, "cli": cli_update, "mcp": mcp_update},
        "archives": {
            "rest": rest_archive,
            "cli": cli_archive,
            "mcp": mcp_archive,
        },
        "audit": audit,
    }
    (artifact_dir / "projects-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_decision_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    triplet = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="decision",
        mcp_request_start=130,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in triplet["projects"].items()
    }

    rest_create_response = httpx.post(
        f"{api_base_url}/decisions",
        headers=auth,
        json={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["rest"],
            "title": "REST Decision",
            "statement": "REST decision statement",
            "rationale": "REST rationale",
        },
        timeout=10,
    )
    rest_create_response.raise_for_status()
    rest_create = rest_create_response.json()
    cli_create = _run_cli(
        [
            "decision",
            "create",
            "--attached-to-kind",
            "project",
            "--attached-to-id",
            project_ids["cli"],
            "--title",
            "CLI Decision",
            "--statement",
            "CLI decision statement",
            "--rationale",
            "CLI rationale",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_create = _call_mcp_tool(
        mcp_base_url,
        "create_decision",
        132,
        api_key=founder_key,
        arguments={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["mcp"],
            "title": "MCP Decision",
            "statement": "MCP decision statement",
            "rationale": "MCP rationale",
            "agent_identity": "parity-decision",
        },
    )

    rest_decision_id = rest_create["decision"]["id"]
    cli_decision_id = cli_create["decision"]["id"]
    mcp_decision_id = mcp_create["decision"]["id"]

    for payload, expected_title in (
        (rest_create, "REST Decision"),
        (cli_create, "CLI Decision"),
        (mcp_create, "MCP Decision"),
    ):
        assert payload["decision"]["attached_to_kind"] == "project"
        assert payload["decision"]["title"] == expected_title

    rest_list = _get_json(
        f"{api_base_url}/decisions",
        api_key=founder_key,
        params={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["rest"],
        },
    )
    cli_list = _run_cli(
        [
            "decision",
            "list",
            "--attached-to-kind",
            "project",
            "--attached-to-id",
            project_ids["cli"],
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_decisions",
        133,
        api_key=founder_key,
        arguments={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["mcp"],
            "agent_identity": "parity-decision",
        },
    )
    assert [item["id"] for item in rest_list["items"]] == [rest_decision_id]
    assert [item["id"] for item in cli_list["items"]] == [cli_decision_id]
    assert [item["id"] for item in mcp_list["items"]] == [mcp_decision_id]

    rest_get = _get_json(
        f"{api_base_url}/decisions/{rest_decision_id}",
        api_key=founder_key,
    )
    cli_get = _run_cli(
        ["decision", "get", cli_decision_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_get = _call_mcp_tool(
        mcp_base_url,
        "get_decision",
        134,
        api_key=founder_key,
        arguments={
            "decision_id": mcp_decision_id,
            "agent_identity": "parity-decision",
        },
    )
    assert rest_get["visuals"] == cli_get["visuals"] == mcp_get["visuals"] == []

    rest_replacement_response = httpx.post(
        f"{api_base_url}/decisions",
        headers=auth,
        json={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["rest"],
            "title": "REST Replacement",
            "statement": "REST replacement statement",
        },
        timeout=10,
    )
    rest_replacement_response.raise_for_status()
    rest_replacement_id = rest_replacement_response.json()["decision"]["id"]
    cli_replacement = _run_cli(
        [
            "decision",
            "create",
            "--attached-to-kind",
            "project",
            "--attached-to-id",
            project_ids["cli"],
            "--title",
            "CLI Replacement",
            "--statement",
            "CLI replacement statement",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_replacement = _call_mcp_tool(
        mcp_base_url,
        "create_decision",
        135,
        api_key=founder_key,
        arguments={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["mcp"],
            "title": "MCP Replacement",
            "statement": "MCP replacement statement",
            "agent_identity": "parity-decision",
        },
    )
    rest_supersede_response = httpx.post(
        f"{api_base_url}/decisions/{rest_decision_id}/supersede",
        headers=auth,
        json={"replacement_id": rest_replacement_id},
        timeout=10,
    )
    rest_supersede_response.raise_for_status()
    rest_supersede = rest_supersede_response.json()
    cli_supersede = _run_cli(
        [
            "decision",
            "supersede",
            cli_decision_id,
            "--replacement-id",
            cli_replacement["decision"]["id"],
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_supersede = _call_mcp_tool(
        mcp_base_url,
        "supersede_decision",
        136,
        api_key=founder_key,
        arguments={
            "decision_id": mcp_decision_id,
            "replacement_id": mcp_replacement["decision"]["id"],
            "agent_identity": "parity-decision",
        },
    )
    assert rest_supersede["replacement_decision"]["supersedes_decision_id"] == (
        rest_decision_id
    )
    assert cli_supersede["replacement_decision"]["supersedes_decision_id"] == (
        cli_decision_id
    )
    assert mcp_supersede["replacement_decision"]["supersedes_decision_id"] == (
        mcp_decision_id
    )

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    ops = [row["op"] for row in audit["entries"]]
    assert ops.count("create_decision") == 6
    assert ops.count("supersede_decision") == 3

    artifact = {
        "creates": {"rest": rest_create, "cli": cli_create, "mcp": mcp_create},
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
        "gets": {"rest": rest_get, "cli": cli_get, "mcp": mcp_get},
        "supersedes": {
            "rest": rest_supersede,
            "cli": cli_supersede,
            "mcp": mcp_supersede,
        },
        "audit": audit,
    }
    (artifact_dir / "decisions-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_learning_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    triplet = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="learning",
        mcp_request_start=140,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in triplet["projects"].items()
    }

    rest_submit_response = httpx.post(
        f"{api_base_url}/learnings",
        headers=auth,
        json={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["rest"],
            "title": "REST Learning",
            "statement": "REST learning statement",
            "context": "REST context",
        },
        timeout=10,
    )
    rest_submit_response.raise_for_status()
    rest_submit = rest_submit_response.json()
    cli_submit = _run_cli(
        [
            "learning",
            "submit",
            "--attached-to-kind",
            "project",
            "--attached-to-id",
            project_ids["cli"],
            "--title",
            "CLI Learning",
            "--statement",
            "CLI learning statement",
            "--context",
            "CLI context",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_submit = _call_mcp_tool(
        mcp_base_url,
        "submit_learning",
        142,
        api_key=founder_key,
        arguments={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["mcp"],
            "title": "MCP Learning",
            "statement": "MCP learning statement",
            "context": "MCP context",
            "agent_identity": "parity-learning",
        },
    )
    rest_learning_id = rest_submit["learning"]["id"]
    cli_learning_id = cli_submit["learning"]["id"]
    mcp_learning_id = mcp_submit["learning"]["id"]

    rest_list = _get_json(
        f"{api_base_url}/learnings",
        api_key=founder_key,
        params={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["rest"],
        },
    )
    cli_list = _run_cli(
        [
            "learning",
            "list",
            "--attached-to-kind",
            "project",
            "--attached-to-id",
            project_ids["cli"],
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_learnings",
        143,
        api_key=founder_key,
        arguments={
            "attached_to_kind": "project",
            "attached_to_id": project_ids["mcp"],
            "agent_identity": "parity-learning",
        },
    )
    assert [item["id"] for item in rest_list["items"]] == [rest_learning_id]
    assert [item["id"] for item in cli_list["items"]] == [cli_learning_id]
    assert [item["id"] for item in mcp_list["items"]] == [mcp_learning_id]

    rest_get = _get_json(
        f"{api_base_url}/learnings/{rest_learning_id}",
        api_key=founder_key,
    )
    cli_get = _run_cli(
        ["learning", "get", cli_learning_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_get = _call_mcp_tool(
        mcp_base_url,
        "get_learning",
        144,
        api_key=founder_key,
        arguments={
            "learning_id": mcp_learning_id,
            "agent_identity": "parity-learning",
        },
    )
    assert rest_get["visuals"] == cli_get["visuals"] == mcp_get["visuals"] == []

    rest_edit_response = httpx.patch(
        f"{api_base_url}/learnings/{rest_learning_id}",
        headers=auth,
        json={"title": "REST Learning Edited", "context": None},
        timeout=10,
    )
    rest_edit_response.raise_for_status()
    rest_edit = rest_edit_response.json()
    cli_edit = _run_cli(
        [
            "learning",
            "edit",
            cli_learning_id,
            "--title",
            "CLI Learning Edited",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_edit = _call_mcp_tool(
        mcp_base_url,
        "edit_learning",
        145,
        api_key=founder_key,
        arguments={
            "learning_id": mcp_learning_id,
            "title": "MCP Learning Edited",
            "agent_identity": "parity-learning",
        },
    )
    assert rest_edit["learning"]["title"] == "REST Learning Edited"
    assert cli_edit["learning"]["title"] == "CLI Learning Edited"
    assert mcp_edit["learning"]["title"] == "MCP Learning Edited"

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    ops = [row["op"] for row in audit["entries"]]
    assert ops.count("submit_learning") == 3
    assert ops.count("edit_learning") == 3

    artifact = {
        "submits": {"rest": rest_submit, "cli": cli_submit, "mcp": mcp_submit},
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
        "gets": {"rest": rest_get, "cli": cli_get, "mcp": mcp_get},
        "edits": {"rest": rest_edit, "cli": cli_edit, "mcp": mcp_edit},
        "audit": audit,
    }
    (artifact_dir / "learnings-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_pipeline_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    auth = {"Authorization": f"Bearer {founder_key}"}

    rest_project_response = httpx.post(
        f"{api_base_url}/projects",
        headers=auth,
        json={
            "name": "REST Pipeline Project",
            "slug": f"parity-rest-pipeline-{suffix}",
        },
        timeout=10,
    )
    rest_project_response.raise_for_status()
    rest_project_id = rest_project_response.json()["project"]["id"]

    cli_project = _run_cli(
        [
            "project",
            "create",
            "--name",
            "CLI Pipeline Project",
            "--slug",
            f"parity-cli-pipeline-{suffix}",
        ],
        api_base_url,
        api_key=founder_key,
    )
    cli_project_id = cli_project["project"]["id"]

    mcp_project = _call_mcp_tool(
        mcp_base_url,
        "create_project",
        46,
        api_key=founder_key,
        arguments={
            "name": "MCP Pipeline Project",
            "slug": f"parity-mcp-pipeline-{suffix}",
            "agent_identity": "parity-pipeline",
        },
    )
    mcp_project_id = mcp_project["project"]["id"]

    rest_create_response = httpx.post(
        f"{api_base_url}/pipelines",
        headers=auth,
        json={
            "project_id": rest_project_id,
            "name": "REST Pipeline",
        },
        timeout=10,
    )
    rest_create_response.raise_for_status()
    rest_create = rest_create_response.json()
    cli_create = _run_cli(
        [
            "pipeline",
            "create",
            "--project",
            cli_project_id,
            "--name",
            "CLI Pipeline",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_create = _call_mcp_tool(
        mcp_base_url,
        "create_pipeline",
        47,
        api_key=founder_key,
        arguments={
            "project_id": mcp_project_id,
            "name": "MCP Pipeline",
            "agent_identity": "parity-pipeline",
        },
    )
    for payload in (rest_create, cli_create, mcp_create):
        assert payload["pipeline"]["is_template"] is False
        assert payload["pipeline"]["cloned_from_pipeline_id"] is None
        assert payload["pipeline"]["archived_at"] is None

    rest_pipeline_id = rest_create["pipeline"]["id"]
    cli_pipeline_id = cli_create["pipeline"]["id"]
    mcp_pipeline_id = mcp_create["pipeline"]["id"]
    expected_ids = {rest_pipeline_id, cli_pipeline_id, mcp_pipeline_id}

    rest_list = _get_json(
        f"{api_base_url}/pipelines",
        api_key=founder_key,
        params={"limit": 200},
    )
    cli_list = _run_cli(
        ["pipeline", "list", "--limit", "200"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_pipelines",
        48,
        api_key=founder_key,
        arguments={"limit": 200, "agent_identity": "parity-pipeline"},
    )
    for page in (rest_list, cli_list, mcp_list):
        assert expected_ids.issubset({pipeline["id"] for pipeline in page["pipelines"]})

    rest_get = _get_json(
        f"{api_base_url}/pipelines/{rest_pipeline_id}",
        api_key=founder_key,
    )
    cli_get = _run_cli(
        ["pipeline", "get", cli_pipeline_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_get = _call_mcp_tool(
        mcp_base_url,
        "get_pipeline",
        49,
        api_key=founder_key,
        arguments={
            "pipeline_id": mcp_pipeline_id,
            "agent_identity": "parity-pipeline",
        },
    )
    assert rest_get["pipeline"]["id"] == rest_pipeline_id
    assert cli_get["pipeline"]["id"] == cli_pipeline_id
    assert mcp_get["pipeline"]["id"] == mcp_pipeline_id

    rest_update_response = httpx.patch(
        f"{api_base_url}/pipelines/{rest_pipeline_id}",
        headers=auth,
        json={"name": "REST Pipeline v2"},
        timeout=10,
    )
    rest_update_response.raise_for_status()
    rest_update = rest_update_response.json()
    cli_update = _run_cli(
        [
            "pipeline",
            "update",
            cli_pipeline_id,
            "--name",
            "CLI Pipeline v2",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_update = _call_mcp_tool(
        mcp_base_url,
        "update_pipeline",
        50,
        api_key=founder_key,
        arguments={
            "pipeline_id": mcp_pipeline_id,
            "name": "MCP Pipeline v2",
            "agent_identity": "parity-pipeline",
        },
    )
    assert rest_update["pipeline"]["name"] == "REST Pipeline v2"
    assert cli_update["pipeline"]["name"] == "CLI Pipeline v2"
    assert mcp_update["pipeline"]["name"] == "MCP Pipeline v2"

    rest_clone_response = httpx.post(
        f"{api_base_url}/pipelines/{rest_pipeline_id}/clone",
        headers=auth,
        json={"name": "REST Pipeline Clone"},
        timeout=10,
    )
    rest_clone_response.raise_for_status()
    rest_clone = rest_clone_response.json()
    cli_clone = _run_cli(
        [
            "pipeline",
            "clone",
            "--source-id",
            cli_pipeline_id,
            "--name",
            "CLI Pipeline Clone",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_clone = _call_mcp_tool(
        mcp_base_url,
        "clone_pipeline",
        51,
        api_key=founder_key,
        arguments={
            "source_id": mcp_pipeline_id,
            "name": "MCP Pipeline Clone",
            "agent_identity": "parity-pipeline",
        },
    )
    for payload, source_id in (
        (rest_clone, rest_pipeline_id),
        (cli_clone, cli_pipeline_id),
        (mcp_clone, mcp_pipeline_id),
    ):
        assert payload["pipeline"]["is_template"] is False
        assert payload["pipeline"]["cloned_from_pipeline_id"] == source_id
        assert payload["pipeline"]["archived_at"] is None
        assert payload["jobs"] == []

    rest_clone_id = rest_clone["pipeline"]["id"]
    cli_clone_id = cli_clone["pipeline"]["id"]
    mcp_clone_id = mcp_clone["pipeline"]["id"]
    rest_archive_response = httpx.post(
        f"{api_base_url}/pipelines/{rest_clone_id}/archive",
        headers=auth,
        timeout=10,
    )
    rest_archive_response.raise_for_status()
    rest_archive = rest_archive_response.json()
    cli_archive = _run_cli(
        ["pipeline", "archive", cli_clone_id],
        api_base_url,
        api_key=founder_key,
    )
    mcp_archive = _call_mcp_tool(
        mcp_base_url,
        "archive_pipeline",
        52,
        api_key=founder_key,
        arguments={
            "pipeline_id": mcp_clone_id,
            "agent_identity": "parity-pipeline",
        },
    )
    for payload, pipeline_id in (
        (rest_archive, rest_clone_id),
        (cli_archive, cli_clone_id),
        (mcp_archive, mcp_clone_id),
    ):
        assert payload["pipeline"]["id"] == pipeline_id
        assert payload["pipeline"]["archived_at"] is not None

    list_after_archive = _get_json(
        f"{api_base_url}/pipelines",
        api_key=founder_key,
        params={"limit": 200},
    )
    archived_clone_ids = {rest_clone_id, cli_clone_id, mcp_clone_id}
    assert archived_clone_ids.isdisjoint(
        {pipeline["id"] for pipeline in list_after_archive["pipelines"]}
    )

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    pipeline_ops = [
        row["op"]
        for row in audit["entries"]
        if row["op"]
        in {"create_pipeline", "update_pipeline", "clone_pipeline", "archive_pipeline"}
    ]
    assert pipeline_ops.count("create_pipeline") == 3
    assert pipeline_ops.count("update_pipeline") == 3
    assert pipeline_ops.count("clone_pipeline") == 3
    assert pipeline_ops.count("archive_pipeline") == 3

    artifact = {
        "projects": {
            "rest": rest_project_response.json(),
            "cli": cli_project,
            "mcp": mcp_project,
        },
        "creates": {"rest": rest_create, "cli": cli_create, "mcp": mcp_create},
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
        "gets": {"rest": rest_get, "cli": cli_get, "mcp": mcp_get},
        "updates": {"rest": rest_update, "cli": cli_update, "mcp": mcp_update},
        "clones": {"rest": rest_clone, "cli": cli_clone, "mcp": mcp_clone},
        "archives": {"rest": rest_archive, "cli": cli_archive, "mcp": mcp_archive},
        "list_after_archive": list_after_archive,
        "audit": audit,
    }
    (artifact_dir / "pipelines-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_clone_pipeline_matches_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="clone",
        mcp_request_start=60,
    )
    pipeline_ids = fixture["pipeline_ids"]

    rest_clone_response = httpx.post(
        f"{api_base_url}/pipelines/{pipeline_ids['rest']}/clone",
        headers=auth,
        json={"name": "REST Clone Pipeline"},
        timeout=10,
    )
    rest_clone_response.raise_for_status()
    rest_clone = rest_clone_response.json()
    cli_clone = _run_cli(
        [
            "pipeline",
            "clone",
            "--source-id",
            pipeline_ids["cli"],
            "--name",
            "CLI Clone Pipeline",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_clone = _call_mcp_tool(
        mcp_base_url,
        "clone_pipeline",
        62,
        api_key=founder_key,
        arguments={
            "source_id": pipeline_ids["mcp"],
            "name": "MCP Clone Pipeline",
            "agent_identity": "parity-clone",
        },
    )

    for payload, source_id in (
        (rest_clone, pipeline_ids["rest"]),
        (cli_clone, pipeline_ids["cli"]),
        (mcp_clone, pipeline_ids["mcp"]),
    ):
        assert payload["pipeline"]["is_template"] is False
        assert payload["pipeline"]["cloned_from_pipeline_id"] == source_id
        assert payload["pipeline"]["archived_at"] is None
        assert payload["jobs"] == []

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    clone_ops = [row["op"] for row in audit["entries"] if row["op"] == "clone_pipeline"]
    assert len(clone_ops) == 3

    artifact = {
        "fixture": fixture,
        "clones": {"rest": rest_clone, "cli": cli_clone, "mcp": mcp_clone},
        "audit": audit,
    }
    (artifact_dir / "clone-pipeline-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_template_pipeline_is_excluded_from_default_pipeline_lists(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    auth = {"Authorization": f"Bearer {founder_key}"}
    project_response = httpx.post(
        f"{api_base_url}/projects",
        headers=auth,
        json={
            "name": "Template List Project",
            "slug": f"parity-template-{suffix}",
        },
        timeout=10,
    )
    project_response.raise_for_status()
    project = project_response.json()["project"]
    project_id = project["id"]
    visible_pipeline_id = _insert_pipeline_for_listing(
        project_id,
        founder_actor_id,
        "Visible Pipeline",
        is_template=False,
    )
    template_pipeline_id = _insert_pipeline_for_listing(
        project_id,
        founder_actor_id,
        "Hidden Template Pipeline",
        is_template=True,
    )

    rest_list = _get_json(
        f"{api_base_url}/pipelines",
        api_key=founder_key,
        params={"limit": 200},
    )
    cli_list = _run_cli(
        ["pipeline", "list", "--limit", "200"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_pipelines",
        70,
        api_key=founder_key,
        arguments={"limit": 200, "agent_identity": "parity-template"},
    )

    for page in (rest_list, cli_list, mcp_list):
        listed_ids = {pipeline["id"] for pipeline in page["pipelines"]}
        assert visible_pipeline_id in listed_ids
        assert template_pipeline_id not in listed_ids

    artifact = {
        "project": project,
        "visible_pipeline_id": visible_pipeline_id,
        "template_pipeline_id": template_pipeline_id,
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
    }
    (artifact_dir / "template-pipeline-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_archive_pipeline_matches_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="archive",
        mcp_request_start=80,
    )
    pipeline_ids = fixture["pipeline_ids"]

    rest_archive_response = httpx.post(
        f"{api_base_url}/pipelines/{pipeline_ids['rest']}/archive",
        headers=auth,
        timeout=10,
    )
    rest_archive_response.raise_for_status()
    rest_archive = rest_archive_response.json()
    cli_archive = _run_cli(
        ["pipeline", "archive", pipeline_ids["cli"]],
        api_base_url,
        api_key=founder_key,
    )
    mcp_archive = _call_mcp_tool(
        mcp_base_url,
        "archive_pipeline",
        82,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "agent_identity": "parity-archive",
        },
    )

    for payload, pipeline_id in (
        (rest_archive, pipeline_ids["rest"]),
        (cli_archive, pipeline_ids["cli"]),
        (mcp_archive, pipeline_ids["mcp"]),
    ):
        assert payload["pipeline"]["id"] == pipeline_id
        assert payload["pipeline"]["archived_at"] is not None

    rest_list = _get_json(
        f"{api_base_url}/pipelines",
        api_key=founder_key,
        params={"limit": 200},
    )
    cli_list = _run_cli(
        ["pipeline", "list", "--limit", "200"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_pipelines",
        83,
        api_key=founder_key,
        arguments={"limit": 200, "agent_identity": "parity-archive"},
    )
    archived_ids = set(pipeline_ids.values())
    for page in (rest_list, cli_list, mcp_list):
        assert archived_ids.isdisjoint(
            {pipeline["id"] for pipeline in page["pipelines"]}
        )

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    archive_ops = [
        row["op"] for row in audit["entries"] if row["op"] == "archive_pipeline"
    ]
    assert len(archive_ops) == 3

    artifact = {
        "fixture": fixture,
        "archives": {"rest": rest_archive, "cli": cli_archive, "mcp": mcp_archive},
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
        "audit": audit,
    }
    (artifact_dir / "archive-pipeline-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_job_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="job",
        mcp_request_start=90,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }
    pipeline_ids = fixture["pipeline_ids"]
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "job-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    rest_create_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Job",
            "description": "Created through REST",
            "contract": contract,
        },
        timeout=10,
    )
    rest_create_response.raise_for_status()
    rest_create = rest_create_response.json()
    cli_create = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Job",
            "--description",
            "Created through CLI",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_create = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        93,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Job",
            "description": "Created through MCP",
            "contract": contract,
            "agent_identity": "parity-job",
        },
    )

    created = {"rest": rest_create, "cli": cli_create, "mcp": mcp_create}
    for surface, payload in created.items():
        job = payload["job"]
        assert job["pipeline_id"] == pipeline_ids[surface]
        assert job["project_id"] == project_ids[surface]
        assert job["state"] == "ready"
        assert job["contract"] == contract
        assert job["labels"] == []

    job_ids = {
        "rest": rest_create["job"]["id"],
        "cli": cli_create["job"]["id"],
        "mcp": mcp_create["job"]["id"],
    }
    rest_list = _get_json(
        f"{api_base_url}/jobs",
        api_key=founder_key,
        params={
            "project_id": project_ids["rest"],
            "pipeline_id": pipeline_ids["rest"],
            "state": "ready",
            "limit": 100,
        },
    )
    cli_list = _run_cli(
        [
            "job",
            "list",
            "--project",
            project_ids["cli"],
            "--pipeline",
            pipeline_ids["cli"],
            "--state",
            "ready",
            "--limit",
            "100",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_list = _call_mcp_tool(
        mcp_base_url,
        "list_jobs",
        94,
        api_key=founder_key,
        arguments={
            "project_id": project_ids["mcp"],
            "pipeline_id": pipeline_ids["mcp"],
            "state": "ready",
            "limit": 100,
            "agent_identity": "parity-job",
        },
    )
    for surface, page in (
        ("rest", rest_list),
        ("cli", cli_list),
        ("mcp", mcp_list),
    ):
        assert job_ids[surface] in {job["id"] for job in page["jobs"]}

    rest_get = _get_json(
        f"{api_base_url}/jobs/{job_ids['rest']}",
        api_key=founder_key,
    )
    cli_get = _run_cli(
        ["job", "get", job_ids["cli"]],
        api_base_url,
        api_key=founder_key,
    )
    mcp_get = _call_mcp_tool(
        mcp_base_url,
        "get_job",
        95,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "agent_identity": "parity-job",
        },
    )
    for payload in (rest_get, cli_get, mcp_get):
        assert payload["decisions"] == {"direct": [], "inherited": []}
        assert payload["learnings"] == {"direct": [], "inherited": []}

    rest_update_response = httpx.patch(
        f"{api_base_url}/jobs/{job_ids['rest']}",
        headers=auth,
        json={"title": "REST Job v2", "description": "Updated through REST"},
        timeout=10,
    )
    rest_update_response.raise_for_status()
    rest_update = rest_update_response.json()
    cli_update = _run_cli(
        [
            "job",
            "update",
            job_ids["cli"],
            "--title",
            "CLI Job v2",
            "--description",
            "Updated through CLI",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_update = _call_mcp_tool(
        mcp_base_url,
        "update_job",
        96,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "title": "MCP Job v2",
            "description": "Updated through MCP",
            "agent_identity": "parity-job",
        },
    )
    assert rest_update["job"]["title"] == "REST Job v2"
    assert cli_update["job"]["title"] == "CLI Job v2"
    assert mcp_update["job"]["title"] == "MCP Job v2"
    for payload in (rest_update, cli_update, mcp_update):
        assert payload["job"]["state"] == "ready"
        assert payload["job"]["contract"] == contract

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 80},
    )
    job_ops = [
        row["op"]
        for row in audit["entries"]
        if row["op"] in {"create_job", "update_job"}
    ]
    assert job_ops.count("create_job") == 3
    assert job_ops.count("update_job") == 3

    artifact = {
        "fixture": fixture,
        "creates": created,
        "lists": {"rest": rest_list, "cli": cli_list, "mcp": mcp_list},
        "gets": {"rest": rest_get, "cli": cli_get, "mcp": mcp_get},
        "updates": {"rest": rest_update, "cli": cli_update, "mcp": mcp_update},
        "audit": audit,
    }
    (artifact_dir / "jobs-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_list_ready_jobs_matches_rest_cli_mcp_and_no_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="ready",
        mcp_request_start=110,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }
    pipeline_ids = fixture["pipeline_ids"]
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "ready-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Ready Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    rest_job = rest_job_response.json()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Ready Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        113,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Ready Job",
            "contract": contract,
            "agent_identity": "parity-ready",
        },
    )
    job_ids = {
        "rest": rest_job["job"]["id"],
        "cli": cli_job["job"]["id"],
        "mcp": mcp_job["job"]["id"],
    }

    rest_label_response = httpx.post(
        f"{api_base_url}/projects/{project_ids['rest']}/labels",
        headers=auth,
        json={"name": "area:web"},
        timeout=10,
    )
    rest_label_response.raise_for_status()
    cli_label = _run_cli(
        ["label", "register", "--project", project_ids["cli"], "--name", "area:web"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_label = _call_mcp_tool(
        mcp_base_url,
        "register_label",
        114,
        api_key=founder_key,
        arguments={
            "project_id": project_ids["mcp"],
            "name": "area:web",
            "agent_identity": "parity-ready",
        },
    )

    rest_attach_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/labels",
        headers=auth,
        json={"label_name": "area:web"},
        timeout=10,
    )
    rest_attach_response.raise_for_status()
    cli_attach = _run_cli(
        ["label", "attach", job_ids["cli"], "--name", "area:web"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_attach = _call_mcp_tool(
        mcp_base_url,
        "attach_label",
        115,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "label_name": "area:web",
            "agent_identity": "parity-ready",
        },
    )

    rest_ready = _get_json(
        f"{api_base_url}/jobs/ready",
        api_key=founder_key,
        params={"project": project_ids["rest"], "label": "area:web", "limit": 100},
    )
    cli_ready = _run_cli(
        [
            "job",
            "list-ready",
            "--project",
            project_ids["cli"],
            "--label",
            "area:web",
            "--limit",
            "100",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_ready = _call_mcp_tool(
        mcp_base_url,
        "list_ready_jobs",
        116,
        api_key=founder_key,
        arguments={
            "project_id": project_ids["mcp"],
            "label_filter": ["area:web"],
            "limit": 100,
            "agent_identity": "parity-ready",
        },
    )
    ready_pages = {"rest": rest_ready, "cli": cli_ready, "mcp": mcp_ready}
    for surface, page in ready_pages.items():
        ready_ids = {job["id"] for job in page["jobs"]}
        assert job_ids[surface] in ready_ids
        assert all(job["state"] == "ready" for job in page["jobs"])
        assert all(job["project_id"] == project_ids[surface] for job in page["jobs"])

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 100},
    )
    assert [row for row in audit["entries"] if row["op"] == "list_ready_jobs"] == []

    artifact = {
        "fixture": fixture,
        "jobs": {"rest": rest_job, "cli": cli_job, "mcp": mcp_job},
        "labels": {
            "rest": rest_label_response.json(),
            "cli": cli_label,
            "mcp": mcp_label,
        },
        "attaches": {
            "rest": rest_attach_response.json(),
            "cli": cli_attach,
            "mcp": mcp_attach,
        },
        "ready_pages": ready_pages,
        "audit": audit,
    }
    (artifact_dir / "ready-jobs-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_claim_next_job_matches_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="claim",
        mcp_request_start=120,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }
    pipeline_ids = fixture["pipeline_ids"]
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "claim-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Claim Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    rest_job = rest_job_response.json()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Claim Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        123,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Claim Job",
            "contract": contract,
            "agent_identity": "parity-claim",
        },
    )

    rest_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": project_ids["rest"]},
        timeout=10,
    )
    rest_claim_response.raise_for_status()
    rest_claim = rest_claim_response.json()
    cli_claim = _run_cli(
        ["job", "claim", "--project", project_ids["cli"]],
        api_base_url,
        api_key=founder_key,
    )
    mcp_claim_result = _call_mcp_tool_result(
        mcp_base_url,
        "claim_next_job",
        124,
        api_key=founder_key,
        arguments={
            "project_id": project_ids["mcp"],
            "agent_identity": "parity-claim",
        },
    )
    mcp_claim = mcp_claim_result["structuredContent"]

    claims = {"rest": rest_claim, "cli": cli_claim, "mcp": mcp_claim}
    source_jobs = {"rest": rest_job, "cli": cli_job, "mcp": mcp_job}
    for surface, claim in claims.items():
        job = claim["job"]
        assert job["id"] == source_jobs[surface]["job"]["id"]
        assert job["state"] == "in_progress"
        assert job["project_id"] == project_ids[surface]
        assert job["pipeline_id"] == pipeline_ids[surface]
        assert job["claimed_by_actor_id"] == founder_actor_id
        assert job["claimed_at"] == job["claim_heartbeat_at"]
        assert claim["packet"] == {
            "project_id": project_ids[surface],
            "pipeline_id": pipeline_ids[surface],
            "current_job_id": job["id"],
            "previous_jobs": [],
            "next_job_id": None,
        }
        assert claim["lease_seconds"] == 900
        assert claim["recommended_heartbeat_after_seconds"] == 30
        assert parse_utc(claim["lease_expires_at"]) == parse_utc(
            job["claimed_at"]
        ) + timedelta(seconds=900)

    mcp_content = mcp_claim_result["content"]
    assert isinstance(mcp_content, list)
    assert len(mcp_content) == 3
    assert json.loads(mcp_content[0]["text"]) == {"job": mcp_claim["job"]}
    assert json.loads(mcp_content[1]["text"]) == {"packet": mcp_claim["packet"]}
    assert "heartbeat_job" in mcp_content[2]["text"]

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 120},
    )
    claim_ops = [row for row in audit["entries"] if row["op"] == "claim_next_job"]
    assert len(claim_ops) == 3
    assert all(row["target_kind"] == "job" for row in claim_ops)
    assert all(row["target_id"] is not None for row in claim_ops)
    assert all(row["error_code"] is None for row in claim_ops)

    artifact = {
        "fixture": fixture,
        "jobs": source_jobs,
        "claims": claims,
        "mcp_content": mcp_content,
        "audit": audit,
    }
    (artifact_dir / "claim-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_heartbeat_job_matches_rest_cli_mcp_and_successes_do_not_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="heartbeat",
        mcp_request_start=160,
    )
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }
    pipeline_ids = fixture["pipeline_ids"]
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "heartbeat-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Heartbeat Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Heartbeat Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        162,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Heartbeat Job",
            "contract": contract,
            "agent_identity": "parity-heartbeat",
        },
    )

    rest_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": project_ids["rest"]},
        timeout=10,
    )
    rest_claim_response.raise_for_status()
    claims = {
        "rest": rest_claim_response.json(),
        "cli": _run_cli(
            ["job", "claim", "--project", project_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "claim_next_job",
            163,
            api_key=founder_key,
            arguments={
                "project_id": project_ids["mcp"],
                "agent_identity": "parity-heartbeat",
            },
        ),
    }
    source_jobs = {
        "rest": rest_job_response.json(),
        "cli": cli_job,
        "mcp": mcp_job,
    }
    for surface, claim in claims.items():
        assert claim["job"]["id"] == source_jobs[surface]["job"]["id"]
        assert claim["job"]["state"] == "in_progress"

    rest_heartbeat_response = httpx.post(
        f"{api_base_url}/jobs/{claims['rest']['job']['id']}/heartbeat",
        headers=auth,
        timeout=10,
    )
    rest_heartbeat_response.raise_for_status()
    heartbeats = {
        "rest": rest_heartbeat_response.json(),
        "cli": _run_cli(
            ["job", "heartbeat", claims["cli"]["job"]["id"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "heartbeat_job",
            164,
            api_key=founder_key,
            arguments={
                "job_id": claims["mcp"]["job"]["id"],
                "agent_identity": "parity-heartbeat",
            },
        ),
    }

    for surface, payload in heartbeats.items():
        job = payload["job"]
        claimed_job = claims[surface]["job"]
        assert job["id"] == claimed_job["id"]
        assert job["state"] == "in_progress"
        assert job["claimed_by_actor_id"] == founder_actor_id
        assert job["claimed_at"] == claimed_job["claimed_at"]
        assert parse_utc(job["claim_heartbeat_at"]) > parse_utc(
            claimed_job["claim_heartbeat_at"]
        )

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 160},
    )
    heartbeat_ops = [row for row in audit["entries"] if row["op"] == "heartbeat_job"]
    assert heartbeat_ops == []
    claim_ops = [row for row in audit["entries"] if row["op"] == "claim_next_job"]
    assert len(claim_ops) == 3

    artifact = {
        "fixture": fixture,
        "jobs": source_jobs,
        "claims": claims,
        "heartbeats": heartbeats,
        "audit": audit,
    }
    (artifact_dir / "heartbeat-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_release_and_reset_claim_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "release-reset-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    release_fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="release",
        mcp_request_start=140,
    )
    release_pipeline_ids = release_fixture["pipeline_ids"]
    release_project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in release_fixture["projects"].items()
    }
    rest_release_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": release_pipeline_ids["rest"],
            "title": "REST Release Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_release_job_response.raise_for_status()
    cli_release_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            release_pipeline_ids["cli"],
            "--title",
            "CLI Release Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_release_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        142,
        api_key=founder_key,
        arguments={
            "pipeline_id": release_pipeline_ids["mcp"],
            "title": "MCP Release Job",
            "contract": contract,
            "agent_identity": "parity-release-reset",
        },
    )

    rest_release_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": release_project_ids["rest"]},
        timeout=10,
    )
    rest_release_claim_response.raise_for_status()
    release_claims = {
        "rest": rest_release_claim_response.json(),
        "cli": _run_cli(
            ["job", "claim", "--project", release_project_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "claim_next_job",
            143,
            api_key=founder_key,
            arguments={
                "project_id": release_project_ids["mcp"],
                "agent_identity": "parity-release-reset",
            },
        ),
    }
    release_job_ids = {
        "rest": rest_release_claim_response.json()["job"]["id"],
        "cli": release_claims["cli"]["job"]["id"],
        "mcp": release_claims["mcp"]["job"]["id"],
    }
    assert release_job_ids["rest"] == rest_release_job_response.json()["job"]["id"]
    assert release_job_ids["cli"] == cli_release_job["job"]["id"]
    assert release_job_ids["mcp"] == mcp_release_job["job"]["id"]

    rest_release_response = httpx.post(
        f"{api_base_url}/jobs/{release_job_ids['rest']}/release",
        headers=auth,
        timeout=10,
    )
    rest_release_response.raise_for_status()
    releases = {
        "rest": rest_release_response.json(),
        "cli": _run_cli(
            ["job", "release", release_job_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "release_job",
            144,
            api_key=founder_key,
            arguments={
                "job_id": release_job_ids["mcp"],
                "agent_identity": "parity-release-reset",
            },
        ),
    }
    for surface, payload in releases.items():
        assert payload["job"]["id"] == release_job_ids[surface]
        assert payload["job"]["state"] == "ready"
        assert payload["job"]["claimed_by_actor_id"] is None
        assert payload["job"]["claimed_at"] is None
        assert payload["job"]["claim_heartbeat_at"] is None

    reset_fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="reset",
        mcp_request_start=150,
    )
    reset_pipeline_ids = reset_fixture["pipeline_ids"]
    reset_project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in reset_fixture["projects"].items()
    }
    rest_reset_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": reset_pipeline_ids["rest"],
            "title": "REST Reset Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_reset_job_response.raise_for_status()
    cli_reset_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            reset_pipeline_ids["cli"],
            "--title",
            "CLI Reset Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_reset_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        152,
        api_key=founder_key,
        arguments={
            "pipeline_id": reset_pipeline_ids["mcp"],
            "title": "MCP Reset Job",
            "contract": contract,
            "agent_identity": "parity-release-reset",
        },
    )
    rest_reset_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": reset_project_ids["rest"]},
        timeout=10,
    )
    rest_reset_claim_response.raise_for_status()
    reset_claims = {
        "rest": rest_reset_claim_response.json(),
        "cli": _run_cli(
            ["job", "claim", "--project", reset_project_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "claim_next_job",
            153,
            api_key=founder_key,
            arguments={
                "project_id": reset_project_ids["mcp"],
                "agent_identity": "parity-release-reset",
            },
        ),
    }
    reset_job_ids = {
        "rest": reset_claims["rest"]["job"]["id"],
        "cli": reset_claims["cli"]["job"]["id"],
        "mcp": reset_claims["mcp"]["job"]["id"],
    }
    assert reset_job_ids["rest"] == rest_reset_job_response.json()["job"]["id"]
    assert reset_job_ids["cli"] == cli_reset_job["job"]["id"]
    assert reset_job_ids["mcp"] == mcp_reset_job["job"]["id"]

    reset_reason = "parity reset"
    rest_reset_response = httpx.post(
        f"{api_base_url}/jobs/{reset_job_ids['rest']}/reset-claim",
        headers=auth,
        json={"reason": reset_reason},
        timeout=10,
    )
    rest_reset_response.raise_for_status()
    resets = {
        "rest": rest_reset_response.json(),
        "cli": _run_cli(
            ["job", "reset-claim", reset_job_ids["cli"], "--reason", reset_reason],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "reset_claim",
            154,
            api_key=founder_key,
            arguments={
                "job_id": reset_job_ids["mcp"],
                "reason": reset_reason,
                "agent_identity": "parity-release-reset",
            },
        ),
    }
    for surface, payload in resets.items():
        assert payload["job"]["id"] == reset_job_ids[surface]
        assert payload["job"]["state"] == "ready"
        assert payload["job"]["claimed_by_actor_id"] is None
        assert payload["job"]["claimed_at"] is None
        assert payload["job"]["claim_heartbeat_at"] is None

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 200},
    )
    release_ops = [row for row in audit["entries"] if row["op"] == "release_job"]
    reset_ops = [row for row in audit["entries"] if row["op"] == "reset_claim"]
    assert len(release_ops) == 3
    assert len(reset_ops) == 3
    assert all(row["target_kind"] == "job" for row in release_ops)
    assert all(row["target_id"] is not None for row in release_ops)
    assert all(row["error_code"] is None for row in release_ops)
    assert all(row["target_kind"] == "job" for row in reset_ops)
    assert all(row["target_id"] is not None for row in reset_ops)
    assert all(row["error_code"] is None for row in reset_ops)
    assert all(row["request_payload"]["reason"] == reset_reason for row in reset_ops)

    artifact = {
        "release_fixture": release_fixture,
        "release_claims": release_claims,
        "releases": releases,
        "reset_fixture": reset_fixture,
        "reset_claims": reset_claims,
        "resets": resets,
        "audit": audit,
    }
    (artifact_dir / "release-reset-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_submit_job_done_matches_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    dod_id = "submit-done-parity"
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": dod_id}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="submit-done",
        mcp_request_start=170,
    )
    pipeline_ids = fixture["pipeline_ids"]
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Submit Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Submit Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        172,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Submit Job",
            "contract": contract,
            "agent_identity": "parity-submit-done",
        },
    )

    rest_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": project_ids["rest"]},
        timeout=10,
    )
    rest_claim_response.raise_for_status()
    claims = {
        "rest": rest_claim_response.json(),
        "cli": _run_cli(
            ["job", "claim", "--project", project_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "claim_next_job",
            173,
            api_key=founder_key,
            arguments={
                "project_id": project_ids["mcp"],
                "agent_identity": "parity-submit-done",
            },
        ),
    }
    job_ids = {
        "rest": claims["rest"]["job"]["id"],
        "cli": claims["cli"]["job"]["id"],
        "mcp": claims["mcp"]["job"]["id"],
    }
    assert job_ids["rest"] == rest_job_response.json()["job"]["id"]
    assert job_ids["cli"] == cli_job["job"]["id"]
    assert job_ids["mcp"] == mcp_job["job"]["id"]

    submit_payload = _submit_done_payload(dod_id)
    rest_submit_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/submit",
        headers=auth,
        json=submit_payload,
        timeout=10,
    )
    rest_submit_response.raise_for_status()
    submits = {
        "rest": rest_submit_response.json(),
        "cli": _run_cli(
            [
                "job",
                "submit",
                job_ids["cli"],
                "--outcome",
                "done",
                "--payload",
                json.dumps(submit_payload, separators=(",", ":")),
            ],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "submit_job",
            174,
            api_key=founder_key,
            arguments={
                "job_id": job_ids["mcp"],
                "payload": submit_payload,
                "agent_identity": "parity-submit-done",
            },
        ),
    }

    for surface, payload in submits.items():
        job = payload["job"]
        assert job["id"] == job_ids[surface]
        assert job["state"] == "done"
        assert job["claimed_by_actor_id"] is None
        assert job["claimed_at"] is None
        assert job["claim_heartbeat_at"] is None
        assert payload["created_decisions"] == []
        assert payload["created_learnings"] == []
        assert payload["created_gated_on_edge"] is False
        assert "audit_row_id" not in payload

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 200},
    )
    submit_ops = [row for row in audit["entries"] if row["op"] == "submit_job"]
    assert len(submit_ops) == 3
    assert all(row["target_kind"] == "job" for row in submit_ops)
    assert {row["target_id"] for row in submit_ops} == set(job_ids.values())
    assert all(row["error_code"] is None for row in submit_ops)
    assert all(row["request_payload"]["outcome"] == "done" for row in submit_ops)
    assert all(row["response_payload"]["outcome"] == "done" for row in submit_ops)

    artifact = {
        "fixture": fixture,
        "claims": claims,
        "submits": submits,
        "audit": audit,
    }
    (artifact_dir / "submit-done-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_submit_pending_review_submit_failed_submit_blocked_parity(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}

    outcomes: dict[str, dict[str, Any]] = {}
    for index, outcome in enumerate(("pending_review", "failed", "blocked")):
        label = outcome.replace("_", "-")
        dod_id = f"submit-{outcome}-parity"
        contract = {"contract_type": "coding-task", "dod_items": [{"id": dod_id}]}
        contract_json = json.dumps(contract, separators=(",", ":"))
        fixture = _create_pipeline_triplet(
            api_base_url,
            mcp_base_url,
            founder_key,
            label=f"submit-{label}",
            mcp_request_start=180 + index * 10,
        )
        pipeline_ids = fixture["pipeline_ids"]
        project_ids = {
            surface: payload["project"]["id"]
            for surface, payload in fixture["projects"].items()
        }

        rest_job_response = httpx.post(
            f"{api_base_url}/jobs",
            headers=auth,
            json={
                "pipeline_id": pipeline_ids["rest"],
                "title": f"REST Submit {outcome} Job",
                "contract": contract,
            },
            timeout=10,
        )
        rest_job_response.raise_for_status()
        cli_job = _run_cli(
            [
                "job",
                "create",
                "--pipeline",
                pipeline_ids["cli"],
                "--title",
                f"CLI Submit {outcome} Job",
                "--contract-json",
                contract_json,
            ],
            api_base_url,
            api_key=founder_key,
        )
        mcp_job = _call_mcp_tool(
            mcp_base_url,
            "create_job",
            182 + index * 10,
            api_key=founder_key,
            arguments={
                "pipeline_id": pipeline_ids["mcp"],
                "title": f"MCP Submit {outcome} Job",
                "contract": contract,
                "agent_identity": f"parity-submit-{outcome}",
            },
        )

        gated_job_ids: dict[str, str] = {}
        if outcome == "blocked":
            for surface, pipeline_id in pipeline_ids.items():
                if surface == "rest":
                    response = httpx.post(
                        f"{api_base_url}/jobs",
                        headers=auth,
                        json={
                            "pipeline_id": pipeline_id,
                            "title": "REST Gating Job",
                            "contract": contract,
                        },
                        timeout=10,
                    )
                    response.raise_for_status()
                    gated_job_ids[surface] = response.json()["job"]["id"]
                elif surface == "cli":
                    response = _run_cli(
                        [
                            "job",
                            "create",
                            "--pipeline",
                            pipeline_id,
                            "--title",
                            "CLI Gating Job",
                            "--contract-json",
                            contract_json,
                        ],
                        api_base_url,
                        api_key=founder_key,
                    )
                    gated_job_ids[surface] = response["job"]["id"]
                else:
                    response = _call_mcp_tool(
                        mcp_base_url,
                        "create_job",
                        183 + index * 10,
                        api_key=founder_key,
                        arguments={
                            "pipeline_id": pipeline_id,
                            "title": "MCP Gating Job",
                            "contract": contract,
                            "agent_identity": f"parity-submit-{outcome}",
                        },
                    )
                    gated_job_ids[surface] = response["job"]["id"]

        rest_claim_response = httpx.post(
            f"{api_base_url}/jobs/claim",
            headers=auth,
            json={"project_id": project_ids["rest"]},
            timeout=10,
        )
        rest_claim_response.raise_for_status()
        claims = {
            "rest": rest_claim_response.json(),
            "cli": _run_cli(
                ["job", "claim", "--project", project_ids["cli"]],
                api_base_url,
                api_key=founder_key,
            ),
            "mcp": _call_mcp_tool(
                mcp_base_url,
                "claim_next_job",
                184 + index * 10,
                api_key=founder_key,
                arguments={
                    "project_id": project_ids["mcp"],
                    "agent_identity": f"parity-submit-{outcome}",
                },
            ),
        }
        job_ids = {
            "rest": claims["rest"]["job"]["id"],
            "cli": claims["cli"]["job"]["id"],
            "mcp": claims["mcp"]["job"]["id"],
        }
        assert job_ids["rest"] == rest_job_response.json()["job"]["id"]
        assert job_ids["cli"] == cli_job["job"]["id"]
        assert job_ids["mcp"] == mcp_job["job"]["id"]

        if outcome == "pending_review":
            submit_payloads = {
                surface: _submit_pending_review_payload(dod_id)
                for surface in ("rest", "cli", "mcp")
            }
        elif outcome == "failed":
            submit_payloads = {
                surface: _submit_failed_payload()
                for surface in ("rest", "cli", "mcp")
            }
        else:
            submit_payloads = {
                surface: _submit_blocked_payload(gated_job_ids[surface])
                for surface in ("rest", "cli", "mcp")
            }

        rest_submit_response = httpx.post(
            f"{api_base_url}/jobs/{job_ids['rest']}/submit",
            headers=auth,
            json=submit_payloads["rest"],
            timeout=10,
        )
        rest_submit_response.raise_for_status()
        submits = {
            "rest": rest_submit_response.json(),
            "cli": _run_cli(
                [
                    "job",
                    "submit",
                    job_ids["cli"],
                    "--outcome",
                    outcome,
                    "--payload",
                    json.dumps(submit_payloads["cli"], separators=(",", ":")),
                ],
                api_base_url,
                api_key=founder_key,
            ),
            "mcp": _call_mcp_tool(
                mcp_base_url,
                "submit_job",
                185 + index * 10,
                api_key=founder_key,
                arguments={
                    "job_id": job_ids["mcp"],
                    "payload": submit_payloads["mcp"],
                    "agent_identity": f"parity-submit-{outcome}",
                },
            ),
        }

        for surface, payload in submits.items():
            job = payload["job"]
            assert job["id"] == job_ids[surface]
            assert job["state"] == outcome
            assert job["claimed_by_actor_id"] is None
            assert job["claimed_at"] is None
            assert job["claim_heartbeat_at"] is None
            assert payload["created_decisions"] == []
            assert payload["created_learnings"] == []
            assert payload["created_gated_on_edge"] is (outcome == "blocked")

        outcomes[outcome] = {
            "fixture": fixture,
            "claims": claims,
            "submits": submits,
            "gated_job_ids": gated_job_ids,
        }

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 300},
    )
    submit_ops = [row for row in audit["entries"] if row["op"] == "submit_job"]
    for outcome in ("pending_review", "failed", "blocked"):
        matching = [
            row
            for row in submit_ops
            if row["response_payload"].get("outcome") == outcome
        ]
        assert len(matching) == 3
        assert all(row["target_kind"] == "job" for row in matching)
        assert all(row["error_code"] is None for row in matching)

    artifact = {"outcomes": outcomes, "audit": audit}
    (artifact_dir / "submit-other-outcomes-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_review_complete_parity(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    dod_id = "review-complete-parity"
    contract = {"contract_type": "coding-task", "dod_items": [{"id": dod_id}]}
    contract_json = json.dumps(contract, separators=(",", ":"))
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="review-complete",
        mcp_request_start=220,
    )
    pipeline_ids = fixture["pipeline_ids"]
    project_ids = {
        surface: payload["project"]["id"]
        for surface, payload in fixture["projects"].items()
    }

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Review Complete Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Review Complete Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        222,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Review Complete Job",
            "contract": contract,
            "agent_identity": "parity-review-complete",
        },
    )

    rest_claim_response = httpx.post(
        f"{api_base_url}/jobs/claim",
        headers=auth,
        json={"project_id": project_ids["rest"]},
        timeout=10,
    )
    rest_claim_response.raise_for_status()
    claims = {
        "rest": rest_claim_response.json(),
        "cli": _run_cli(
            ["job", "claim", "--project", project_ids["cli"]],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "claim_next_job",
            224,
            api_key=founder_key,
            arguments={
                "project_id": project_ids["mcp"],
                "agent_identity": "parity-review-complete",
            },
        ),
    }
    job_ids = {
        "rest": claims["rest"]["job"]["id"],
        "cli": claims["cli"]["job"]["id"],
        "mcp": claims["mcp"]["job"]["id"],
    }
    assert job_ids["rest"] == rest_job_response.json()["job"]["id"]
    assert job_ids["cli"] == cli_job["job"]["id"]
    assert job_ids["mcp"] == mcp_job["job"]["id"]

    submit_payload = _submit_pending_review_payload(dod_id)
    rest_submit_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/submit",
        headers=auth,
        json=submit_payload,
        timeout=10,
    )
    rest_submit_response.raise_for_status()
    submits = {
        "rest": rest_submit_response.json(),
        "cli": _run_cli(
            [
                "job",
                "submit",
                job_ids["cli"],
                "--outcome",
                "pending_review",
                "--payload",
                json.dumps(submit_payload, separators=(",", ":")),
            ],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "submit_job",
            225,
            api_key=founder_key,
            arguments={
                "job_id": job_ids["mcp"],
                "payload": submit_payload,
                "agent_identity": "parity-review-complete",
            },
        ),
    }
    assert all(
        payload["job"]["state"] == "pending_review" for payload in submits.values()
    )

    review_payload = _review_complete_payload()
    rest_review_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/review-complete",
        headers=auth,
        json=review_payload,
        timeout=10,
    )
    rest_review_response.raise_for_status()
    reviews = {
        "rest": rest_review_response.json(),
        "cli": _run_cli(
            [
                "job",
                "review-complete",
                job_ids["cli"],
                "--final-outcome",
                "done",
                "--notes",
                str(review_payload["notes"]),
            ],
            api_base_url,
            api_key=founder_key,
        ),
        "mcp": _call_mcp_tool(
            mcp_base_url,
            "review_complete",
            226,
            api_key=founder_key,
            arguments={
                "job_id": job_ids["mcp"],
                "final_outcome": "done",
                "notes": review_payload["notes"],
                "agent_identity": "parity-review-complete",
            },
        ),
    }
    for surface, payload in reviews.items():
        assert payload["job"]["id"] == job_ids[surface]
        assert payload["job"]["state"] == "done"

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 300},
    )
    review_ops = [row for row in audit["entries"] if row["op"] == "review_complete"]
    assert len(review_ops) == 3
    assert {row["target_id"] for row in review_ops} == set(job_ids.values())
    assert all(row["target_kind"] == "job" for row in review_ops)
    assert all(row["error_code"] is None for row in review_ops)
    assert all(row["request_payload"]["final_outcome"] == "done" for row in review_ops)
    assert all(
        row["response_payload"]["final_outcome"] == "done" for row in review_ops
    )

    artifact = {
        "fixture": fixture,
        "claims": claims,
        "submits": submits,
        "reviews": reviews,
        "audit": audit,
    }
    (artifact_dir / "review-complete-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_comment_and_cancel_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    auth = {"Authorization": f"Bearer {founder_key}"}
    fixture = _create_pipeline_triplet(
        api_base_url,
        mcp_base_url,
        founder_key,
        label="comment",
        mcp_request_start=100,
    )
    pipeline_ids = fixture["pipeline_ids"]
    contract = {
        "contract_type": "coding-task",
        "dod_items": [{"id": "comment-cancel-parity"}],
    }
    contract_json = json.dumps(contract, separators=(",", ":"))

    rest_job_response = httpx.post(
        f"{api_base_url}/jobs",
        headers=auth,
        json={
            "pipeline_id": pipeline_ids["rest"],
            "title": "REST Comment Job",
            "contract": contract,
        },
        timeout=10,
    )
    rest_job_response.raise_for_status()
    rest_job = rest_job_response.json()
    cli_job = _run_cli(
        [
            "job",
            "create",
            "--pipeline",
            pipeline_ids["cli"],
            "--title",
            "CLI Comment Job",
            "--contract-json",
            contract_json,
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_job = _call_mcp_tool(
        mcp_base_url,
        "create_job",
        103,
        api_key=founder_key,
        arguments={
            "pipeline_id": pipeline_ids["mcp"],
            "title": "MCP Comment Job",
            "contract": contract,
            "agent_identity": "parity-comment",
        },
    )
    job_ids = {
        "rest": rest_job["job"]["id"],
        "cli": cli_job["job"]["id"],
        "mcp": mcp_job["job"]["id"],
    }

    rest_comment_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/comments",
        headers=auth,
        json={"body": "REST durable note"},
        timeout=10,
    )
    rest_comment_response.raise_for_status()
    rest_comment = rest_comment_response.json()
    cli_comment = _run_cli(
        ["job", "comment", job_ids["cli"], "--body", "CLI durable note"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_comment = _call_mcp_tool(
        mcp_base_url,
        "comment_on_job",
        104,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "body": "MCP durable note",
            "agent_identity": "parity-comment",
        },
    )
    assert rest_comment["comment"]["body"] == "REST durable note"
    assert cli_comment["comment"]["body"] == "CLI durable note"
    assert mcp_comment["comment"]["body"] == "MCP durable note"

    rest_comments = _get_json(
        f"{api_base_url}/jobs/{job_ids['rest']}/comments",
        api_key=founder_key,
        params={"limit": 100},
    )
    cli_comments = _run_cli(
        ["job", "comments", job_ids["cli"], "--limit", "100"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_comments = _call_mcp_tool(
        mcp_base_url,
        "list_job_comments",
        105,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "limit": 100,
            "agent_identity": "parity-comment",
        },
    )
    assert [comment["body"] for comment in rest_comments["comments"]] == [
        "REST durable note"
    ]
    assert [comment["body"] for comment in cli_comments["comments"]] == [
        "CLI durable note"
    ]
    assert [comment["body"] for comment in mcp_comments["comments"]] == [
        "MCP durable note"
    ]

    rest_cancel_response = httpx.post(
        f"{api_base_url}/jobs/{job_ids['rest']}/cancel",
        headers=auth,
        timeout=10,
    )
    rest_cancel_response.raise_for_status()
    rest_cancel = rest_cancel_response.json()
    cli_cancel = _run_cli(
        ["job", "cancel", job_ids["cli"]],
        api_base_url,
        api_key=founder_key,
    )
    mcp_cancel = _call_mcp_tool(
        mcp_base_url,
        "cancel_job",
        106,
        api_key=founder_key,
        arguments={
            "job_id": job_ids["mcp"],
            "agent_identity": "parity-comment",
        },
    )
    for payload in (rest_cancel, cli_cancel, mcp_cancel):
        assert payload["job"]["state"] == "cancelled"

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 100},
    )
    comment_ops = [row for row in audit["entries"] if row["op"] == "comment_on_job"]
    cancel_ops = [row for row in audit["entries"] if row["op"] == "cancel_job"]
    assert len(comment_ops) == 3
    assert len(cancel_ops) == 3
    for row in comment_ops:
        assert "body_length" in row["request_payload"]
        assert "body" not in row["request_payload"]
        assert "body" not in row["response_payload"]

    artifact = {
        "fixture": fixture,
        "jobs": {"rest": rest_job, "cli": cli_job, "mcp": mcp_job},
        "comments": {
            "rest": rest_comment,
            "cli": cli_comment,
            "mcp": mcp_comment,
        },
        "comment_lists": {
            "rest": rest_comments,
            "cli": cli_comments,
            "mcp": mcp_comments,
        },
        "cancels": {"rest": rest_cancel, "cli": cli_cancel, "mcp": mcp_cancel},
        "audit": audit,
    }
    (artifact_dir / "comments-cancel-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )


def test_label_ops_match_rest_cli_mcp_and_audit(
    api_base_url: str,
    mcp_base_url: str,
    founder_key: str,
    founder_actor_id: str,
    artifact_dir: Path,
    redact_evidence: Any,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    auth = {"Authorization": f"Bearer {founder_key}"}
    project_response = httpx.post(
        f"{api_base_url}/projects",
        headers=auth,
        json={
            "name": "Label Parity Project",
            "slug": f"parity-label-{suffix}",
        },
        timeout=10,
    )
    project_response.raise_for_status()
    project = project_response.json()["project"]
    project_id = project["id"]
    job_id = _insert_pipeline_job(project_id, founder_actor_id, "Label parity job")

    rest_register_response = httpx.post(
        f"{api_base_url}/projects/{project_id}/labels",
        headers=auth,
        json={"name": "area:web", "color": "#336699"},
        timeout=10,
    )
    rest_register_response.raise_for_status()
    rest_register = rest_register_response.json()
    cli_register = _run_cli(
        [
            "label",
            "register",
            "--project",
            project_id,
            "--name",
            "prio:high",
        ],
        api_base_url,
        api_key=founder_key,
    )
    mcp_register = _call_mcp_tool(
        mcp_base_url,
        "register_label",
        30,
        api_key=founder_key,
        arguments={
            "project_id": project_id,
            "name": "kind:test",
            "agent_identity": "parity-label",
        },
    )

    rest_attach_response = httpx.post(
        f"{api_base_url}/jobs/{job_id}/labels",
        headers=auth,
        json={"label_name": "area:web"},
        timeout=10,
    )
    rest_attach_response.raise_for_status()
    rest_attach = rest_attach_response.json()
    cli_attach = _run_cli(
        ["label", "attach", job_id, "--name", "prio:high"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_attach = _call_mcp_tool(
        mcp_base_url,
        "attach_label",
        31,
        api_key=founder_key,
        arguments={
            "job_id": job_id,
            "label_name": "kind:test",
            "agent_identity": "parity-label",
        },
    )
    assert set(rest_attach["labels"]) == {"area:web"}
    assert set(cli_attach["labels"]) == {"area:web", "prio:high"}
    assert set(mcp_attach["labels"]) == {"area:web", "prio:high", "kind:test"}

    rest_detach_response = httpx.delete(
        f"{api_base_url}/jobs/{job_id}/labels/area:web",
        headers=auth,
        timeout=10,
    )
    rest_detach_response.raise_for_status()
    rest_detach = rest_detach_response.json()
    cli_detach = _run_cli(
        ["label", "detach", job_id, "--name", "prio:high"],
        api_base_url,
        api_key=founder_key,
    )
    mcp_detach = _call_mcp_tool(
        mcp_base_url,
        "detach_label",
        32,
        api_key=founder_key,
        arguments={
            "job_id": job_id,
            "label_name": "kind:test",
            "agent_identity": "parity-label",
        },
    )
    assert set(rest_detach["labels"]) == {"prio:high", "kind:test"}
    assert set(cli_detach["labels"]) == {"kind:test"}
    assert mcp_detach["labels"] == []

    audit = _get_json(
        f"{api_base_url}/audit",
        api_key=founder_key,
        params={"actor": founder_actor_id, "limit": 50},
    )
    label_ops = [
        row["op"]
        for row in audit["entries"]
        if row["op"] in {"register_label", "attach_label", "detach_label"}
    ]
    assert label_ops.count("register_label") == 3
    assert label_ops.count("attach_label") == 3
    assert label_ops.count("detach_label") == 3

    artifact = {
        "project": project,
        "job_id": job_id,
        "register": {
            "rest": rest_register,
            "cli": cli_register,
            "mcp": mcp_register,
        },
        "attach": {"rest": rest_attach, "cli": cli_attach, "mcp": mcp_attach},
        "detach": {"rest": rest_detach, "cli": cli_detach, "mcp": mcp_detach},
        "audit": audit,
    }
    (artifact_dir / "labels-parity.txt").write_text(
        redact_evidence(_json_text(artifact)),
        encoding="utf-8",
    )
