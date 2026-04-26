# Capability #1: Four-surface ping — execution plan

Plan for: [capabilities.md](capabilities.md)
Brief: [brief.md](brief.md)
Plane parent: **AQ2-1** ([Plane link](http://localhost:8502/mmmmm/projects/bcdf1ac3-fc45-4186-8ad0-f3b6c21ceec8/issues/29d87829-2af1-49fc-9693-f5170a36b626/))
ADR-AQ-019 lexicon · ADR-AQ-030 contract structure

---

## Capability statement (verbatim from `capabilities.md`)

> A canonical operation contract (`HealthStatus` plus `VersionInfo`)
> round-trips identically through REST, CLI, MCP, and the read-only UI,
> all rendered from one Pydantic model.

## Depends on

None (first capability).

## Locked decisions for this capability

1. **Run AQ 2.0 in parallel with AQ1.** AQ1 stays running on host
   (api:8010, web:3100, db:54329, pgbouncer:64329). AQ 2.0 takes free
   ports: **api:8001, web:3002**. No collision.
2. **Mirror AQ1's monorepo layout** (`apps/api`, `apps/cli`, `apps/web`,
   root `pyproject.toml` + `package.json` + `docker-compose.yml`). Caveat:
   AQ1 never fully shipped, so we use the *structure* but don't inherit
   the assumption that the implementation worked.
3. **Docker + CI from cap #1.** `docker-compose.yml` and
   `.github/workflows/{lint,test,build,parity}.yml` ship as part of this
   capability. The parity CI is itself part of the moat — it has to exist
   before we trust later caps.
4. **Strict DoD evidence per ADR-AQ-030.** Every DoD item lands a real
   artifact in `plans/v2-rebuild/artifacts/cap-01/`. Screenshots as PNGs,
   command outputs as `.txt`, OpenAPI as `.json`, CI run URLs in the
   submission payload. No prose-only evidence anywhere.

## Stack

- Python 3.12, uv-managed (`uv.lock` committed), Ruff for lint, mypy --strict for typing, pytest for test
- FastAPI + Pydantic v2 (auto-OpenAPI emitted)
- **MCP transports per ADR-AQ-021:**
  - stdio: separate `aq-mcp` console-script binary (defined in `pyproject.toml`), used by stdio-based MCP clients
  - streamable HTTP: mounted inside the FastAPI process at `/mcp`
  - SSE: **deferred to v1.1** — explicitly out of scope for cap #1; recorded as deviation in submission `risks_or_deviations`
- Typer CLI calling REST (use `uv run aq` in scripts; bare `aq` only after `uv sync`)
- Next.js 15 + Tailwind + shadcn/ui (read-only); pnpm-managed (`pnpm-lock.yaml` committed)
- TypeScript types generated from a **committed OpenAPI snapshot** (`tests/parity/openapi.snapshot.json`) via `openapi-typescript` — never fetched from a live server during build (so Docker / CI builds don't depend on a running API)
- Web UI proxies to API via Next.js route handlers `app/api/health/route.ts` and `app/api/version/route.ts` (no browser-direct cross-origin calls; no CORS work)
- Docker compose: 2 services (api + web), no DB
- GitHub Actions: lint + test + build + parity (parity workflow runs after parity tests exist — see story order)
- Playwright for UI smoke test + screenshot

## Stories

Each story → one Plane child ticket parented to **AQ2-1** → one PR.

| # | Story | Plane ticket |
|---|---|---|
| 1.1 | Repo scaffold — root config files (pyproject, package.json, docker-compose, LICENSE, README, AUTHORS, CODEOWNERS, .gitignore, .editorconfig, .gitattributes) | AQ2-3 |
| 1.2 | Shared Pydantic contract — `HealthStatus` and `VersionInfo` in `apps/api/src/aq_api/models/health.py` | AQ2-4 |
| 1.3 | FastAPI app — `GET /healthz` + `GET /version` rendering from the Pydantic models | AQ2-5 |
| 1.4 | FastMCP at `/mcp` — `health_check` + `get_version` MCP tools using the same models | AQ2-6 |
| 1.5 | Typer CLI — `aq health` + `aq version` calling REST, identical payload | AQ2-7 |
| 1.6 | Next.js read-only page — renders both payloads, TypeScript types from OpenAPI | AQ2-8 |
| 1.7 | Dockerfiles + docker-compose — both services build and run with healthchecks | AQ2-9 |
| 1.8 | Parity tests — OpenAPI snapshot + MCP schema snapshot + REST↔CLI + REST↔MCP + Web↔REST | AQ2-10 |
| 1.9 | GitHub Actions — lint + test + build + parity workflows (depends on parity tests existing) | AQ2-11 |

Stories land in dependency order. Each story's PR closes its child ticket
via Rule 6 (self-merge after CI green). The capability is approved at the
end via the validation script + artifacts, not per-PR.

## Story details

### Story 1.1 — Repo scaffold

Root files:

- `pyproject.toml` — uv-managed, defines workspace + scripts (`aq`, `aq-mcp`)
- `uv.lock` — committed; reproducible Python deps
- `package.json` — Node workspace declaration pointing at `apps/web`
- `pnpm-workspace.yaml` — Node workspace config
- `pnpm-lock.yaml` — committed; reproducible Node deps
- `docker-compose.yml` — `api` (`apps/api`, `8001:8000`) + `web` (`apps/web`, `3002:3000`); no DB
- `.dockerignore` (root) — exclude `.git`, `node_modules`, `.venv`, `__pycache__`, `plans/v2-rebuild/artifacts/`
- `.gitignore` — Python + Node + Next.js standards; ignores `app/types/api.ts` (codegen output) and ad-hoc `.tmp/` per AGENTS.md Rule 4
- `.editorconfig` — LF, 2-space JSON/YAML, 4-space Python
- `.gitattributes` — LF for shell + YAML
- `LICENSE` — Apache-2.0 with `Copyright 2026 Mario Watson` header
- `README.md` — first line: `Created by **[Mario Watson](https://github.com/mario-watson)** · Apache-2.0`
- `AUTHORS.md` — Mario Watson as creator
- `.github/CODEOWNERS` — `* @mario-watson`
- `tests/parity/openapi.snapshot.json` — placeholder (real snapshot generated in Story 1.3)
- `tests/parity/mcp_schema.snapshot.json` — placeholder (real snapshot generated in Story 1.4)
- `scripts/validate-cap01.sh` — bash validation runner (Git Bash / WSL / CI-Linux)
- `scripts/validate-cap01.ps1` — PowerShell validation runner (Windows native shell)
- Subdirectories with `.gitkeep` so empty dirs are tracked: `apps/api/`, `apps/cli/`, `apps/web/`, `tests/parity/`, `plans/v2-rebuild/artifacts/cap-01/`

### Story 1.2 — Shared Pydantic contract

`apps/api/src/aq_api/models/health.py`:

```python
class HealthStatus(BaseModel):
    status: Literal["ok"]
    timestamp: datetime  # UTC, isoformat

class VersionInfo(BaseModel):
    version: str   # semver
    commit: str    # git short SHA
    built_at: datetime
```

Single source of truth for all four surfaces.

### Story 1.3 — FastAPI app

`apps/api/src/aq_api/app.py`:

- `GET /healthz` → `HealthStatus`
- `GET /version` → `VersionInfo`
- OpenAPI auto-emitted at `/openapi.json`
- `uvicorn aq_api.app:app --host 0.0.0.0 --port 8000`

### Story 1.4 — FastMCP transports per ADR-AQ-021

`apps/api/src/aq_api/mcp.py`:

- `health_check()` MCP tool returns `HealthStatus`
- `get_version()` MCP tool returns `VersionInfo`
- Tool schemas auto-derived from same Pydantic models

**Transport layout (per ADR-AQ-021):**
- **stdio:** separate console-script binary `aq-mcp` (defined in `pyproject.toml` as `aq-mcp = "aq_api.mcp:stdio_main"`). Stdio is *not* mounted at `/mcp` because stdio doesn't have a URL.
- **streamable HTTP:** mounted inside the FastAPI app at `/mcp`.
- **SSE:** **deferred to v1.1.** Recorded as deviation in cap #1 submission `risks_or_deviations`. ADR-AQ-021 lists three transports; we ship two now.

### Story 1.5 — Typer CLI

`apps/cli/src/aq_cli/main.py`:

- `aq health` calls `GET ${AQ_API_URL}/healthz`, prints JSON
- `aq version` calls `GET ${AQ_API_URL}/version`, prints JSON
- Reads `AQ_API_URL` env var (default `http://localhost:8001`)
- `pyproject.toml` script: `aq = "aq_cli.main:app"`
- **Invocation in scripts:** always `uv run aq ...` (not bare `aq`) so the venv is guaranteed active. Bare `aq` is only safe after `uv sync` in an interactive shell.

### Story 1.6 — Next.js read-only page

`apps/web/`:

- Next.js 15 + Tailwind + shadcn/ui (config files: `next.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.js`)
- Single page at `/` showing `HealthStatus` and `VersionInfo`
- **TypeScript types generated from `tests/parity/openapi.snapshot.json`** (committed file) via `openapi-typescript` — never live HTTP at build time. `pnpm gen:types` script reads the snapshot and writes `app/types/api.ts`. Snapshot regeneration is a separate explicit step (gated to schema bumps).
- **API access via Next.js route handlers** — UI fetches `/api/health` and `/api/version`; route handlers proxy to `${AQ_API_URL}` (default `http://api:8000` in Docker, override `http://localhost:8001` for local dev). No browser-direct cross-origin calls. No CORS in v1.
- No buttons, no forms — pure read

### Story 1.7 — Dockerfiles + compose

- `apps/api/Dockerfile` — uv-based Python, multistage, runs uvicorn. Final stage installs `curl` for the compose healthcheck (or uses `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"` if we want to skip the curl install — choose curl for readability).
- `apps/web/Dockerfile` — Node-based, builds Next.js, serves with `next start`. Final stage installs `wget` (BusyBox `wget -q -O- ...`) for the healthcheck or uses `node -e "fetch('http://localhost:3000/api/health').then(r=>process.exit(r.ok?0:1))"`.
- **Build context = repo root.** `docker-compose.yml` declares `build: { context: ., dockerfile: apps/api/Dockerfile }` for the `api` service and `build: { context: ., dockerfile: apps/web/Dockerfile }` for `web`. The Dockerfiles `COPY` `pyproject.toml` / `uv.lock` / `package.json` / `pnpm-lock.yaml` from root so the lockfile-based reproducible builds work; per-app `Dockerfile`s with their own scoped context cannot see the root lockfiles.
- `docker-compose.yml` defines `api` + `web` on a shared bridge network. Healthcheck for `api`: `CMD-SHELL`, `curl -fsS http://localhost:8000/healthz || exit 1`. Healthcheck for `web`: `CMD-SHELL`, `wget -q -O- http://localhost:3000/api/health || exit 1`. Both with `interval=5s`, `timeout=3s`, `retries=6`, `start_period=10s`.
- `web` `depends_on: { api: { condition: service_healthy } }` so the Next.js proxy route handlers don't 502 on cold start.

### Story 1.8 — Parity tests (was Story 1.9; swapped per Codex review)

`tests/parity/test_four_surface_parity.py`:

- **OpenAPI snapshot test** — `GET /openapi.json` matches committed snapshot at `tests/parity/openapi.snapshot.json`
- **MCP schema snapshot test** — call MCP `list_tools` over streamable HTTP; assert input/output schemas match `tests/parity/mcp_schema.snapshot.json`
- **REST ↔ CLI parity** — boot API, call `GET /healthz`, run `uv run aq health`, assert structural equality on `status` (byte-equal) and `timestamp` (valid ISO UTC, within 5s of test start)
- **REST ↔ MCP parity** — call MCP `health_check` over streamable HTTP and `GET /healthz`; assert `status` byte-equal, `timestamp` within 5s; for version, `version` + `commit` + `built_at` must be byte-equal (no clock involved)
- **Web ↔ REST parity** — Playwright navigates to Next.js page, reads rendered DOM, asserts `status` + `version` + `commit` byte-equal with REST; `timestamp` within 5s

**Timestamp parity rule (Codex correction):** `HealthStatus.timestamp` is per-call and intentionally not byte-equal across the four surfaces. Tests assert "valid ISO UTC, within ±5s of test start" instead of byte-equality. `VersionInfo.built_at` IS byte-equal across surfaces because it's read from the `AQ_BUILT_AT` env var stamped at container build time.

### Story 1.9 — CI workflows (was Story 1.8; depends on parity tests existing)

`.github/workflows/`:

- `lint.yml` — Ruff (Python) + ESLint (TypeScript)
- `test.yml` — pytest (Python) + Playwright (web e2e)
- `build.yml` — `docker compose build` smoke
- `parity.yml` — runs the parity test suite from Story 1.8 (which lands first)

## DoD list (ADR-AQ-030 format)

Every DoD item maps to one or more artifacts in
`plans/v2-rebuild/artifacts/cap-01/`. The submission payload's
`dod_results[]` array references each `dod_id` with a terminal status and
evidence pointer.

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| `DOD-CAP01-01` | Repo scaffold files exist and parse correctly | `command` | `artifacts/cap-01/scaffold-validation.txt` showing all root files present + parseable; commit SHA in `git-shas.txt` | All file checks exit 0 |
| `DOD-CAP01-02` | Pydantic contract models compile and pass mypy | `command` | `artifacts/cap-01/typecheck.txt` showing `mypy apps/api/src/aq_api/models/` clean | mypy exit 0 |
| `DOD-CAP01-03` | FastAPI `/healthz` returns valid `HealthStatus` | `command` | `artifacts/cap-01/rest-healthz.txt` containing real `curl -i` output, HTTP 200, JSON body | HTTP 200 + payload validates against Pydantic |
| `DOD-CAP01-04` | FastAPI `/version` returns valid `VersionInfo` | `command` | `artifacts/cap-01/rest-version.txt` containing real `curl -i` output | HTTP 200 + payload validates |
| `DOD-CAP01-05` | OpenAPI spec emitted and matches committed snapshot | `artifact` | `artifacts/cap-01/openapi.json` and snapshot diff is empty | `diff` exits 0 |
| `DOD-CAP01-06` | MCP `health_check` + `get_version` callable, same payloads | `test` | `artifacts/cap-01/mcp-health.txt` + `mcp-version.txt`; pytest report at `parity-test-report.xml` | Pytest passes |
| `DOD-CAP01-07` | CLI `aq health` + `aq version` return same JSON as REST | `command` | `artifacts/cap-01/cli-health.txt` + `cli-version.txt`; payload-equality assertion in pytest | exit 0 + JSON equality |
| `DOD-CAP01-08` | Next.js page renders both payloads | `artifact` + `reviewer_check` | `artifacts/cap-01/ui-health.png` Playwright screenshot; `ui-health.html` snapshot | Screenshot exists; panel `status` + `version` + `commit` + `built_at` byte-match REST; `timestamp` is valid ISO UTC within ±5s of REST response (Playwright assertion) |
| `DOD-CAP01-09` | `docker compose up` brings both services up healthy | `command` | `artifacts/cap-01/docker-build.txt` + `docker-up.txt` + `docker-healthcheck.txt` | Both services pass healthcheck within 30s |
| `DOD-CAP01-10` | GitHub Actions: lint + test + build + parity all green | `artifact` | `artifacts/cap-01/ci-run.txt` containing run URLs + `gh run view` summary for **all four** workflows (lint, test, build, parity) | All four workflows show conclusion `success` |
| `DOD-CAP01-11` | Parity test suite passes (5 tests) | `test` | `artifacts/cap-01/parity-test-report.xml` (JUnit XML) | All 5 parity tests pass |
| `DOD-CAP01-12` | All four surfaces return semantically identical payloads | `test` | `artifacts/cap-01/four-surface-equivalence.txt` showing structural equality | `version` + `commit` + `built_at` byte-equal across REST + CLI + MCP + UI rendered values; `status` byte-equal on `HealthStatus`; `timestamp` is valid ISO UTC and within ±5s window (per timestamp parity rule). UI rendered values match REST. |

## Validation scripts

Two equivalent runners committed in Story 1.1:

- `scripts/validate-cap01.sh` — bash; for Git Bash / WSL / CI Linux runners
- `scripts/validate-cap01.ps1` — PowerShell; for Windows native shell

**Both runners write the same artifacts to `plans/v2-rebuild/artifacts/cap-01/`.** The bash version is canonical (CI runs it); the .ps1 version is a parallel Windows implementation maintained in lockstep so a developer on either OS can validate locally.

Bash version (canonical):

```bash
mkdir -p plans/v2-rebuild/artifacts/cap-01

# 1. Repo scaffold check (DOD-CAP01-01)
{
  test -f pyproject.toml && echo "ok pyproject.toml"
  test -f package.json && echo "ok package.json"
  test -f docker-compose.yml && echo "ok docker-compose.yml"
  test -f LICENSE && head -1 LICENSE | grep -q "Copyright 2026 Mario Watson" && echo "ok LICENSE"
  test -f README.md && head -3 README.md | grep -q "Mario Watson" && echo "ok README.md"
  test -f AUTHORS.md && echo "ok AUTHORS.md"
  test -f .github/CODEOWNERS && grep -q "@mario-watson" .github/CODEOWNERS && echo "ok CODEOWNERS"
  python -c "import tomllib; tomllib.loads(open('pyproject.toml').read())" && echo "ok pyproject.toml parses"
  node -e "JSON.parse(require('fs').readFileSync('package.json'))" && echo "ok package.json parses"
} | tee plans/v2-rebuild/artifacts/cap-01/scaffold-validation.txt

git log --oneline -10 > plans/v2-rebuild/artifacts/cap-01/git-shas.txt

# 2. Typecheck (DOD-CAP01-02)
uv run mypy apps/api/src/aq_api/models/ \
  | tee plans/v2-rebuild/artifacts/cap-01/typecheck.txt

# 3. Build + run (DOD-CAP01-09)
docker compose build 2>&1 | tee plans/v2-rebuild/artifacts/cap-01/docker-build.txt
docker compose up -d 2>&1 | tee plans/v2-rebuild/artifacts/cap-01/docker-up.txt
until curl -sf http://localhost:8001/healthz; do sleep 1; done
echo "API healthy at $(date -u +%FT%TZ)" \
  | tee plans/v2-rebuild/artifacts/cap-01/docker-healthcheck.txt

# 4. REST (DOD-CAP01-03, 04)
curl -i http://localhost:8001/healthz \
  | tee plans/v2-rebuild/artifacts/cap-01/rest-healthz.txt
curl -i http://localhost:8001/version \
  | tee plans/v2-rebuild/artifacts/cap-01/rest-version.txt

# 5. CLI (DOD-CAP01-07) — use `uv run aq` so the venv is active
AQ_API_URL=http://localhost:8001 uv run aq health \
  | tee plans/v2-rebuild/artifacts/cap-01/cli-health.txt
AQ_API_URL=http://localhost:8001 uv run aq version \
  | tee plans/v2-rebuild/artifacts/cap-01/cli-version.txt

# 6. MCP (DOD-CAP01-06)
uv run python -m tests.parity.mcp_harness health_check \
  | tee plans/v2-rebuild/artifacts/cap-01/mcp-health.txt
uv run python -m tests.parity.mcp_harness get_version \
  | tee plans/v2-rebuild/artifacts/cap-01/mcp-version.txt

# 7. UI screenshot (DOD-CAP01-08)
cd apps/web && pnpm playwright test e2e/health.spec.ts && cd ../..

# 8. OpenAPI snapshot (DOD-CAP01-05)
curl -s http://localhost:8001/openapi.json | jq . \
  > plans/v2-rebuild/artifacts/cap-01/openapi.json
diff plans/v2-rebuild/artifacts/cap-01/openapi.json \
     tests/parity/openapi.snapshot.json

# 9. Parity test suite (DOD-CAP01-11)
uv run pytest tests/parity/ \
  --junit-xml=plans/v2-rebuild/artifacts/cap-01/parity-test-report.xml

# 10. Four-surface equivalence (DOD-CAP01-12)
uv run python tests/parity/four_surface_diff.py \
  > plans/v2-rebuild/artifacts/cap-01/four-surface-equivalence.txt

# 11. CI runs (DOD-CAP01-10) — capture all four workflows after PR push
{
  echo "## lint"; gh run list --workflow=lint.yml --limit=1 --json url,status,conclusion
  echo "## test"; gh run list --workflow=test.yml --limit=1 --json url,status,conclusion
  echo "## build"; gh run list --workflow=build.yml --limit=1 --json url,status,conclusion
  echo "## parity"; gh run list --workflow=parity.yml --limit=1 --json url,status,conclusion
} > plans/v2-rebuild/artifacts/cap-01/ci-run.txt
```

## Done when

- All 9 child story tickets (AQ2-3 through AQ2-11) are merged.
- Validation script runs end-to-end against the running services.
- All 12 DoD items have `status="passed"` with evidence pointers in
  `plans/v2-rebuild/artifacts/cap-01/`.
- Submission payload references every DoD id and points each at a real
  artifact (no prose-only evidence).
- Capability #1 marked `[DONE]` in `capabilities.md` with the validation
  date and a one-line `## Log` entry.
- Ghost approves capability #2 to start.

## Risks / deviations to declare in submission

- Port choice (8001/3002) — if either is later occupied by another service, document actual ports used.
- Mypy may need narrow `# type: ignore` for FastMCP integration if the library lacks types — document each occurrence.
- **MCP SSE transport deferred to v1.1** per ADR-AQ-021. Cap #1 ships stdio (via `aq-mcp` binary) + streamable HTTP at `/mcp` only. SSE is recorded as a deviation, not a missing-and-forgotten gap.
- **Artifact files committed.** Per AGENTS.md Rule 8 (no fabricated evidence), artifacts under `plans/v2-rebuild/artifacts/cap-01/` are **committed to git** as the durable evidence trail. The `parity-test-report.xml` and screenshots are reviewable in the PR diff; later capabilities can reference them by commit SHA.

## Out of scope

- No domain entities (Project / Workflow / Pipeline / Job) — cap #3.
- No auth — cap #2.
- No Postgres — cap #2 (audit log).
- No Run Ledger — cap #7.
- No Context Packet — cap #8.
- No Decisions / Learnings — cap #9.
- No real four-view UI — cap #11. (Cap #1's UI is a single health page,
  not the four views.)

## Implementation order (high level)

Per execution recommendation: single branch `aq2-cap-01`, story-by-story commits, ONE PR at the end.

1. Story 1.1 (scaffold) → commit
2. Story 1.2 (Pydantic models) → commit
3. Story 1.3 (FastAPI app + initial OpenAPI snapshot generated) → commit
4. Story 1.4 (FastMCP — stdio + streamable HTTP; initial MCP schema snapshot) → commit
5. Story 1.5 (Typer CLI) → commit
6. Story 1.6 (Next.js page; types codegen from committed snapshot; proxy via /api/health + /api/version) → commit
7. Story 1.7 (Dockerfiles + compose) → commit
8. **Story 1.8 (Parity tests — must exist before CI references them)** → commit
9. **Story 1.9 (CI workflows — references parity test files committed in Story 1.8)** → commit
10. Run validation script (bash on Linux/CI/Git Bash, .ps1 on Windows native) end-to-end, save and **commit** all artifacts to `plans/v2-rebuild/artifacts/cap-01/`
11. Open ONE PR for the whole capability; CI runs all four workflows
12. PR green → squash-merge to `main` → submit cap #1 with `outcome=done` referencing every DoD id with its evidence pointer
13. Mark capability #1 `[DONE]` in `capabilities.md` after Ghost approves
