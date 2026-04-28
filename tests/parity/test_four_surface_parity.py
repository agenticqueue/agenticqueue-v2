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
