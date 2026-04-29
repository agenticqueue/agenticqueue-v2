#!/usr/bin/env python3
"""AQ2 three-surface sweep: exercises all 29 live ops via REST, CLI, and MCP.

Usage:
    uv run python scripts/three-surface-sweep.py

Writes evidence to plans/v2-rebuild/artifacts/three-surface-sweep/.
Run scripts/redact-evidence.sh against that directory before committing.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# ── Config ────────────────────────────────────────────────────────────────────

VAULT_URL = "http://127.0.0.1:8200"
API_BASE = "http://localhost:8001"
MCP_URL = f"{API_BASE}/mcp"
ARTIFACT_DIR = Path("plans/v2-rebuild/artifacts/three-surface-sweep")
DOCKER_COMPOSE = ["docker", "compose"]
MINIMAL_CONTRACT = {"dod_items": []}

# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class Ctx:
    surface: str
    prefix: str = field(init=False)
    actor_id: str | None = None
    actor_key_id: str | None = None
    revoke_actor_key: str | None = None   # key plaintext for the revoke-test actor
    revoke_actor_key_id: str | None = None
    project_id: str | None = None
    pipeline_id: str | None = None
    job_id: str | None = None
    label_name: str | None = None

    def __post_init__(self) -> None:
        self.prefix = f"qa-sweep-{self.surface}"


# Short run-id appended to slugs so each sweep run gets fresh, non-conflicting names.
# The name field has no uniqueness constraint; only the slug does.
RUN_ID = secrets.token_hex(3)  # 6 hex chars, e.g. "a3f1c9"

ctx_rest = Ctx("rest")
ctx_cli = Ctx("cli")
ctx_mcp = Ctx("mcp")
ALL_CTX = [ctx_rest, ctx_cli, ctx_mcp]

_results: dict[str, dict[str, str]] = {}
_errors: list[str] = []
_founder_key: str = ""
_mcp_req_id = 0

# ── Key bootstrap ─────────────────────────────────────────────────────────────

def _vault_token() -> str:
    token = os.environ.get("VAULT_TOKEN")
    if token:
        return token
    # Try common locations for the mmmmm .env file
    candidates = [
        Path(r"D:\mmmmm\.env"),
        Path.home().parent.parent / "mmmmm" / ".env",
    ]
    for env_file in candidates:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("VAULT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("VAULT_TOKEN not found — set env var VAULT_TOKEN")


def read_founder_key() -> str:
    resp = requests.get(
        f"{VAULT_URL}/v1/secret/data/aq2/founder-key",
        headers={"X-Vault-Token": _vault_token()},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["data"]["value"]


# ── Surface helpers ───────────────────────────────────────────────────────────

def _rest(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    auth: str | None = None,
) -> tuple[int, Any]:
    key = auth or _founder_key
    headers: dict[str, str] = {"Authorization": f"Bearer {key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(
        method, f"{API_BASE}{path}",
        headers=headers, json=body, params=params, timeout=15,
    )
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, resp.text


def _cli(args: list[str], auth: str | None = None) -> tuple[int, str]:
    key = auth or _founder_key
    # AQ_API_URL must be set explicitly — the container env doesn't carry it
    cmd = DOCKER_COMPOSE + [
        "exec", "-T", "api",
        "env", f"AQ_API_KEY={key}", "AQ_API_URL=http://api:8000",
        "uv", "run", "aq",
    ] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode, (r.stdout + r.stderr).strip()


def _mcp(tool: str, args: dict[str, Any] | None = None, auth: str | None = None) -> tuple[int, Any]:
    global _mcp_req_id
    _mcp_req_id += 1
    key = auth or _founder_key
    payload = {
        "jsonrpc": "2.0", "id": _mcp_req_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}},
    }
    resp = requests.post(
        MCP_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json=payload, timeout=15,
    )
    try:
        data = resp.json()
    except Exception:
        return resp.status_code, resp.text
    # Unwrap FastMCP content envelope
    result = data.get("result", {})
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return resp.status_code, json.loads(content[0]["text"])
        except Exception:
            return resp.status_code, content[0]["text"]
    if "error" in data:
        return resp.status_code, data
    return resp.status_code, result


# ── Artifact helpers ──────────────────────────────────────────────────────────

def _save(op: str, surface: str, sc: int, body: Any) -> None:
    d = ARTIFACT_DIR / op
    d.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(body, indent=2, default=str) if isinstance(body, (dict, list)) else str(body)
    (d / f"{surface}.txt").write_text(f"[HTTP {sc}]\n{raw}\n", encoding="utf-8")


def _rec(op: str, surface: str, status: str) -> None:
    _results.setdefault(op, {})[surface] = status


def _status(sc: int, rc: int, surface: str) -> str:
    if surface == "cli":
        return "PASS" if rc == 0 else f"FAIL(rc={rc})"
    return "PASS" if 200 <= sc < 300 else f"FAIL({sc})"


# ── The 29 ops ────────────────────────────────────────────────────────────────

def op_health_check() -> None:
    op = "health_check"
    sc, body = _rest("GET", "/healthz")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["health"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("health_check")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_get_version() -> None:
    op = "get_version"
    sc, body = _rest("GET", "/version")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["version"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("get_version")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_get_self() -> None:
    op = "get_self"
    sc, body = _rest("GET", "/actors/me")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["whoami"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("get_self")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_actors() -> None:
    op = "list_actors"
    sc, body = _rest("GET", "/actors")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["actor", "list"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("list_actors")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_query_audit_log() -> None:
    op = "query_audit_log"
    sc, body = _rest("GET", "/audit", params={"limit": 5})
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["audit"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("query_audit_log", {"limit": 5})
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_projects() -> None:
    op = "list_projects"
    sc, body = _rest("GET", "/projects")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["project", "list"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("list_projects")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_pipelines() -> None:
    op = "list_pipelines"
    sc, body = _rest("GET", "/pipelines")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["pipeline", "list"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("list_pipelines")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_jobs() -> None:
    op = "list_jobs"
    sc, body = _rest("GET", "/jobs")
    _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
    rc, out = _cli(["job", "list"])
    _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
    sc, body = _mcp("list_jobs")
    _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def _parse_cli_json(out: str) -> Any:
    """Extract the first valid JSON object/array from CLI output (skips bytecode lines)."""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except Exception:
                continue
    # Fallback: try the whole string
    return json.loads(out)


def op_create_actor() -> None:
    op = "create_actor"
    for ctx in ALL_CTX:
        name = f"{ctx.prefix}-actor-{RUN_ID}"
        if ctx.surface == "rest":
            sc, body = _rest("POST", "/actors", {"name": name, "kind": "agent"})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
            if sc == 200:
                ctx.actor_id = body["actor"]["id"]
                ctx.actor_key_id = body["api_key"]["id"]
        elif ctx.surface == "cli":
            rc, out = _cli(["actor", "create", "--name", name, "--kind", "agent"])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
            if rc == 0:
                try:
                    parsed = _parse_cli_json(out)
                    ctx.actor_id = parsed["actor"]["id"]
                    ctx.actor_key_id = parsed["api_key"]["id"]
                except Exception:
                    pass
        else:
            sc, body = _mcp("create_actor", {"name": name, "kind": "agent"})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))
            if sc == 200 and isinstance(body, dict):
                ctx.actor_id = body.get("actor", {}).get("id")
                ctx.actor_key_id = body.get("api_key", {}).get("id")


def _create_revoke_actor(ctx: Ctx) -> None:
    """Create a throwaway actor used solely for the revoke_api_key test."""
    name = f"{ctx.prefix}-revoke-{RUN_ID}"
    if ctx.surface == "rest":
        sc, body = _rest("POST", "/actors", {"name": name, "kind": "agent"})
        if sc == 200:
            ctx.revoke_actor_key = body["key"]
            ctx.revoke_actor_key_id = body["api_key"]["id"]
    elif ctx.surface == "cli":
        rc, out = _cli(["actor", "create", "--name", name, "--kind", "agent"])
        if rc == 0:
            try:
                parsed = _parse_cli_json(out)
                ctx.revoke_actor_key = parsed["key"]
                ctx.revoke_actor_key_id = parsed["api_key"]["id"]
            except Exception:
                pass
    else:
        sc, body = _mcp("create_actor", {"name": name, "kind": "agent"})
        if sc == 200 and isinstance(body, dict):
            ctx.revoke_actor_key = body.get("key")
            ctx.revoke_actor_key_id = body.get("api_key", {}).get("id")


def op_create_project() -> None:
    op = "create_project"
    for ctx in ALL_CTX:
        slug = f"{ctx.prefix}-{RUN_ID}"  # unique per run to avoid archived-slug conflicts
        if ctx.surface == "rest":
            sc, body = _rest("POST", "/projects", {"name": ctx.prefix, "slug": slug})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
            if sc == 200:
                ctx.project_id = body["project"]["id"]
        elif ctx.surface == "cli":
            rc, out = _cli(["project", "create", "--name", ctx.prefix, "--slug", slug])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
            if rc == 0:
                try:
                    ctx.project_id = _parse_cli_json(out)["project"]["id"]
                except Exception:
                    pass
        else:
            sc, body = _mcp("create_project", {"name": ctx.prefix, "slug": slug})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))
            if sc == 200 and isinstance(body, dict):
                ctx.project_id = body.get("project", {}).get("id")


def op_register_label() -> None:
    op = "register_label"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)")
            continue
        lname = f"{ctx.prefix}:sweep"
        ctx.label_name = lname
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/projects/{ctx.project_id}/labels", {"name": lname})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["label", "register", "--project", ctx.project_id, "--name", lname])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("register_label", {"project_id": ctx.project_id, "name": lname})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_create_pipeline() -> None:
    op = "create_pipeline"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)")
            continue
        name = f"{ctx.prefix}-pipeline"
        if ctx.surface == "rest":
            sc, body = _rest("POST", "/pipelines", {"project_id": ctx.project_id, "name": name})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
            if sc == 200:
                ctx.pipeline_id = body["pipeline"]["id"]
        elif ctx.surface == "cli":
            rc, out = _cli(["pipeline", "create", "--project", ctx.project_id, "--name", name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
            if rc == 0:
                try:
                    ctx.pipeline_id = _parse_cli_json(out)["pipeline"]["id"]
                except Exception:
                    pass
        else:
            sc, body = _mcp("create_pipeline", {"project_id": ctx.project_id, "name": name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))
            if sc == 200 and isinstance(body, dict):
                ctx.pipeline_id = body.get("pipeline", {}).get("id")


def op_create_job() -> None:
    op = "create_job"
    for ctx in ALL_CTX:
        if not ctx.pipeline_id:
            _save(op, ctx.surface, 0, "BLOCKED: no pipeline_id")
            _rec(op, ctx.surface, "BLOCKED(no pipeline_id)")
            continue
        title = f"{ctx.prefix} sweep job"
        contract_json = json.dumps(MINIMAL_CONTRACT)
        if ctx.surface == "rest":
            sc, body = _rest("POST", "/jobs", {
                "pipeline_id": ctx.pipeline_id,
                "title": title,
                "contract": MINIMAL_CONTRACT,
            })
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
            if sc == 200:
                ctx.job_id = body["job"]["id"]
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "create",
                            "--pipeline", ctx.pipeline_id,
                            "--title", title,
                            "--contract-json", contract_json])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
            if rc == 0:
                try:
                    ctx.job_id = _parse_cli_json(out)["job"]["id"]
                except Exception:
                    pass
        else:
            sc, body = _mcp("create_job", {
                "pipeline_id": ctx.pipeline_id,
                "title": title,
                "contract": MINIMAL_CONTRACT,
            })
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))
            if sc == 200 and isinstance(body, dict):
                ctx.job_id = body.get("job", {}).get("id")


def op_get_project() -> None:
    op = "get_project"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("GET", f"/projects/{ctx.project_id}")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["project", "get", ctx.project_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("get_project", {"project_id": ctx.project_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_get_pipeline() -> None:
    op = "get_pipeline"
    for ctx in ALL_CTX:
        if not ctx.pipeline_id:
            _save(op, ctx.surface, 0, "BLOCKED: no pipeline_id")
            _rec(op, ctx.surface, "BLOCKED(no pipeline_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("GET", f"/pipelines/{ctx.pipeline_id}")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["pipeline", "get", ctx.pipeline_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("get_pipeline", {"pipeline_id": ctx.pipeline_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_get_job() -> None:
    op = "get_job"
    for ctx in ALL_CTX:
        if not ctx.job_id:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id")
            _rec(op, ctx.surface, "BLOCKED(no job_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("GET", f"/jobs/{ctx.job_id}")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "get", ctx.job_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("get_job", {"job_id": ctx.job_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_ready_jobs() -> None:
    op = "list_ready_jobs"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("GET", "/jobs/ready", params={"project": ctx.project_id})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "list-ready", "--project", ctx.project_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("list_ready_jobs", {"project_id": ctx.project_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_list_job_comments() -> None:
    op = "list_job_comments"
    for ctx in ALL_CTX:
        if not ctx.job_id:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id")
            _rec(op, ctx.surface, "BLOCKED(no job_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("GET", f"/jobs/{ctx.job_id}/comments")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "comments", ctx.job_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("list_job_comments", {"job_id": ctx.job_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_update_project() -> None:
    op = "update_project"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)"); continue
        new_name = f"{ctx.prefix}-updated"
        if ctx.surface == "rest":
            sc, body = _rest("PATCH", f"/projects/{ctx.project_id}", {"name": new_name})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["project", "update", ctx.project_id, "--name", new_name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("update_project", {"project_id": ctx.project_id, "name": new_name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_update_pipeline() -> None:
    op = "update_pipeline"
    for ctx in ALL_CTX:
        if not ctx.pipeline_id:
            _save(op, ctx.surface, 0, "BLOCKED: no pipeline_id")
            _rec(op, ctx.surface, "BLOCKED(no pipeline_id)"); continue
        new_name = f"{ctx.prefix}-pipeline-updated"
        if ctx.surface == "rest":
            sc, body = _rest("PATCH", f"/pipelines/{ctx.pipeline_id}", {"name": new_name})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["pipeline", "update", ctx.pipeline_id, "--name", new_name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("update_pipeline", {"pipeline_id": ctx.pipeline_id, "name": new_name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_update_job() -> None:
    op = "update_job"
    for ctx in ALL_CTX:
        if not ctx.job_id:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id")
            _rec(op, ctx.surface, "BLOCKED(no job_id)"); continue
        new_title = f"{ctx.prefix} sweep job updated"
        if ctx.surface == "rest":
            sc, body = _rest("PATCH", f"/jobs/{ctx.job_id}", {"title": new_title})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "update", ctx.job_id, "--title", new_title])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("update_job", {"job_id": ctx.job_id, "title": new_title})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_attach_label() -> None:
    op = "attach_label"
    for ctx in ALL_CTX:
        if not ctx.job_id or not ctx.label_name:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id or label_name")
            _rec(op, ctx.surface, "BLOCKED(no job_id or label)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/jobs/{ctx.job_id}/labels", {"label_name": ctx.label_name})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["label", "attach", ctx.job_id, "--name", ctx.label_name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("attach_label", {"job_id": ctx.job_id, "label_name": ctx.label_name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_comment_on_job() -> None:
    op = "comment_on_job"
    for ctx in ALL_CTX:
        if not ctx.job_id:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id")
            _rec(op, ctx.surface, "BLOCKED(no job_id)"); continue
        body_text = f"sweep comment from {ctx.surface} surface"
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/jobs/{ctx.job_id}/comments", {"body": body_text})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "comment", ctx.job_id, "--body", body_text])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("comment_on_job", {"job_id": ctx.job_id, "body": body_text})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_detach_label() -> None:
    op = "detach_label"
    for ctx in ALL_CTX:
        if not ctx.job_id or not ctx.label_name:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id or label_name")
            _rec(op, ctx.surface, "BLOCKED(no job_id or label)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("DELETE", f"/jobs/{ctx.job_id}/labels/{ctx.label_name}")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["label", "detach", ctx.job_id, "--name", ctx.label_name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("detach_label", {"job_id": ctx.job_id, "label_name": ctx.label_name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_clone_pipeline() -> None:
    op = "clone_pipeline"
    for ctx in ALL_CTX:
        if not ctx.pipeline_id:
            _save(op, ctx.surface, 0, "BLOCKED: no pipeline_id")
            _rec(op, ctx.surface, "BLOCKED(no pipeline_id)"); continue
        clone_name = f"{ctx.prefix}-pipeline-clone"
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/pipelines/{ctx.pipeline_id}/clone", {"name": clone_name})
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["pipeline", "clone",
                            "--source-id", ctx.pipeline_id, "--name", clone_name])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("clone_pipeline", {"source_id": ctx.pipeline_id, "name": clone_name})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_cancel_job() -> None:
    op = "cancel_job"
    for ctx in ALL_CTX:
        if not ctx.job_id:
            _save(op, ctx.surface, 0, "BLOCKED: no job_id")
            _rec(op, ctx.surface, "BLOCKED(no job_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/jobs/{ctx.job_id}/cancel")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["job", "cancel", ctx.job_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("cancel_job", {"job_id": ctx.job_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_revoke_api_key() -> None:
    """Create a throwaway actor per surface, auth as it, revoke its only key.
    Expects 409 cannot_revoke_last_key — documented last-key-protection behavior.
    Marked PASS(409-expected) since the op fires correctly and protects the actor."""
    op = "revoke_api_key"
    for ctx in ALL_CTX:
        _create_revoke_actor(ctx)
        if not ctx.revoke_actor_key_id or not ctx.revoke_actor_key:
            _save(op, ctx.surface, 0, "BLOCKED: could not mint revoke-test actor")
            _rec(op, ctx.surface, "BLOCKED(mint failed)"); continue
        key_id = ctx.revoke_actor_key_id
        auth = ctx.revoke_actor_key
        if ctx.surface == "rest":
            sc, body = _rest("DELETE", f"/api-keys/{key_id}", auth=auth)
            _save(op, "rest", sc, body)
            # 409 = expected last-key protection; 200 = success if somehow 2 keys
            _rec(op, "rest", "PASS(409-expected)" if sc == 409 else _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["key", "revoke", key_id], auth=auth)
            _save(op, "cli", 0, out)
            # CLI exits non-zero on 409; check output for expected error
            expected = rc != 0 and "cannot_revoke_last_key" in out
            _rec(op, "cli", "PASS(409-expected)" if expected else _status(0, rc, "cli"))
        else:
            sc, body = _mcp("revoke_api_key", {"api_key_id": key_id}, auth=auth)
            _save(op, "mcp", sc, body)
            # MCP wraps tool errors in HTTP 200; detect the expected last-key error
            body_str = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
            last_key_err = "cannot revoke last active key" in body_str or "cannot_revoke_last_key" in body_str
            _rec(op, "mcp", "PASS(409-expected)" if last_key_err else _status(sc, 0, "mcp"))


def op_archive_pipeline() -> None:
    op = "archive_pipeline"
    for ctx in ALL_CTX:
        if not ctx.pipeline_id:
            _save(op, ctx.surface, 0, "BLOCKED: no pipeline_id")
            _rec(op, ctx.surface, "BLOCKED(no pipeline_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/pipelines/{ctx.pipeline_id}/archive")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["pipeline", "archive", ctx.pipeline_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("archive_pipeline", {"pipeline_id": ctx.pipeline_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


def op_archive_project() -> None:
    op = "archive_project"
    for ctx in ALL_CTX:
        if not ctx.project_id:
            _save(op, ctx.surface, 0, "BLOCKED: no project_id")
            _rec(op, ctx.surface, "BLOCKED(no project_id)"); continue
        if ctx.surface == "rest":
            sc, body = _rest("POST", f"/projects/{ctx.project_id}/archive")
            _save(op, "rest", sc, body); _rec(op, "rest", _status(sc, 0, "rest"))
        elif ctx.surface == "cli":
            rc, out = _cli(["project", "archive", ctx.project_id])
            _save(op, "cli", 0, out); _rec(op, "cli", _status(0, rc, "cli"))
        else:
            sc, body = _mcp("archive_project", {"project_id": ctx.project_id})
            _save(op, "mcp", sc, body); _rec(op, "mcp", _status(sc, 0, "mcp"))


# ── Summary + cleanup evidence ────────────────────────────────────────────────

OPS_ORDER = [
    "health_check", "get_version", "get_self",
    "list_actors", "query_audit_log",
    "list_projects", "list_pipelines", "list_jobs",
    "create_actor", "create_project", "register_label",
    "create_pipeline", "create_job",
    "get_project", "get_pipeline", "get_job",
    "list_ready_jobs", "list_job_comments",
    "update_project", "update_pipeline", "update_job",
    "attach_label", "comment_on_job", "detach_label",
    "clone_pipeline",
    "cancel_job", "revoke_api_key",
    "archive_pipeline", "archive_project",
]


def write_summary() -> None:
    lines = [
        "# AQ2 Three-Surface Sweep — SUMMARY",
        "",
        f"Run at: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}",
        "",
        "| op | REST | CLI | MCP |",
        "|---|---|---|---|",
    ]
    for op in OPS_ORDER:
        r = _results.get(op, {})
        lines.append(
            f"| {op} | {r.get('rest', 'UNKNOWN')} | {r.get('cli', 'UNKNOWN')} | {r.get('mcp', 'UNKNOWN')} |"
        )
    if _errors:
        lines += ["", "## Errors", ""]
        lines += [f"- {e}" for e in _errors]
    (ARTIFACT_DIR / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_cleanup_evidence() -> None:
    sc, body = _rest("GET", "/projects", params={"include_archived": "true"})
    lines = [f"[HTTP {sc}] post-sweep project list (all including archived)\n"]
    if isinstance(body, dict):
        for p in body.get("projects", []):
            archived = p.get("archived_at") or "ACTIVE"
            lines.append(f"  {p.get('name', '?')} | id={p.get('id', '?')} | archived={archived}")
    (ARTIFACT_DIR / "cleanup.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    global _founder_key
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    print("Reading founder key from Vault...")
    _founder_key = read_founder_key()
    key_prefix = _founder_key[:8]
    print(f"  key prefix: {key_prefix}***")

    # Canary: start
    print("Canary check (start)...")
    sc, body = _rest("GET", "/actors/me")
    prefix_line = f"key_prefix={key_prefix}***\n"
    canary_content = f"[HTTP {sc}]\n{prefix_line}{json.dumps(body, indent=2, default=str)}\n"
    (ARTIFACT_DIR / "canary-start.txt").write_text(canary_content, encoding="utf-8")
    if sc != 200:
        print(f"  FAIL: canary returned {sc}. Aborting.")
        return 1
    print(f"  PASS [HTTP {sc}]")

    # The 29 ops in order
    sweep_ops = [
        ("health_check",     op_health_check),
        ("get_version",      op_get_version),
        ("get_self",         op_get_self),
        ("list_actors",      op_list_actors),
        ("query_audit_log",  op_query_audit_log),
        ("list_projects",    op_list_projects),
        ("list_pipelines",   op_list_pipelines),
        ("list_jobs",        op_list_jobs),
        ("create_actor",     op_create_actor),
        ("create_project",   op_create_project),
        ("register_label",   op_register_label),
        ("create_pipeline",  op_create_pipeline),
        ("create_job",       op_create_job),
        ("get_project",      op_get_project),
        ("get_pipeline",     op_get_pipeline),
        ("get_job",          op_get_job),
        ("list_ready_jobs",  op_list_ready_jobs),
        ("list_job_comments",op_list_job_comments),
        ("update_project",   op_update_project),
        ("update_pipeline",  op_update_pipeline),
        ("update_job",       op_update_job),
        ("attach_label",     op_attach_label),
        ("comment_on_job",   op_comment_on_job),
        ("detach_label",     op_detach_label),
        ("clone_pipeline",   op_clone_pipeline),
        ("cancel_job",       op_cancel_job),
        ("revoke_api_key",   op_revoke_api_key),
        ("archive_pipeline", op_archive_pipeline),
        ("archive_project",  op_archive_project),
    ]

    for op_name, fn in sweep_ops:
        print(f"  [{op_name}]")
        try:
            fn()
        except Exception as exc:
            _errors.append(f"{op_name}: {exc!r}")
            for surface in ("rest", "cli", "mcp"):
                _results.setdefault(op_name, {}).setdefault(surface, f"FAIL(exc:{exc!r})")
            print(f"    EXCEPTION: {exc!r}")

    # Canary: end
    print("Canary check (end)...")
    sc, body = _rest("GET", "/actors/me")
    canary_content = f"[HTTP {sc}]\n{prefix_line}{json.dumps(body, indent=2, default=str)}\n"
    (ARTIFACT_DIR / "canary-end.txt").write_text(canary_content, encoding="utf-8")
    if sc != 200:
        print(f"  FAIL: end canary returned {sc} — AQ2-53 regression check!")
        return 1
    print(f"  PASS [HTTP {sc}]")

    write_cleanup_evidence()
    write_summary()

    # Driver-run evidence (just this output)
    (ARTIFACT_DIR / "driver-run.txt").write_text(
        "driver exited 0 — see SUMMARY.md for per-op results\n"
    )

    print(f"\nSweep complete. Artifacts: {ARTIFACT_DIR}/")
    print("Next: bash scripts/redact-evidence.sh plans/v2-rebuild/artifacts/three-surface-sweep/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
