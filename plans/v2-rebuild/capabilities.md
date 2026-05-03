# Capabilities — AgenticQueue 2.0 (v2-rebuild)

Status: Capability #1 done — validated 2026-04-26 and merged to main at `96e158d`.
Effort: v2-rebuild
Brief: [brief.md](brief.md)
Lexicon: [ADR-AQ-019](../../../mmmmm-agenticqueue/adrs/ADR-AQ-019-lexicon.md)
Contract structure: [ADR-AQ-030](../../../mmmmm-agenticqueue/adrs/ADR-AQ-030-agent-ready-contract-checklist.md)
Rev: **rev 4 — 2026-04-28** — folded [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) (Decisions 1–6 + Path Y1-Insert / cap #3.5) and [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) (Decision 7 / queryable graph). Workflows collapse into Pipelines; Contract Profiles dropped; Run Ledger collapses into audit_log; cap #3.5 inserted between AQ2-46 and AQ2-47; final v1 op count = 51 (was 56). Both plan-update files are authoritative on conflict with this document until rev 5.

---

## Architecture locks (the Pact)

These constraints govern every capability below.

### Auth model
- **API key = Actor identity.** Each key has a name; audit log attributes mutations to that name. No capability table, no `admin`/`supervisor`/`approve` permissions.
- **API keys are minted in the UI by a human only.** No `create_api_key` on CLI / MCP / REST.
- **API key lookup is HMAC-indexed.** `AQ_KEY_LOOKUP_SECRET` derives non-display `lookup_id` values for O(1) auth lookup; rotating that secret invalidates every existing API key and must be paired with revoking/re-minting all active keys. No dual-secret rotation in v1.
- **Claim leases auto-release on missed heartbeats.** `AQ_CLAIM_LEASE_SECONDS` (default 900) bounds the time an `in_progress` Job can sit without a `heartbeat_job` call before AQ flips it back to `ready` and writes an audit row with `op='claim_auto_release'`, `error_code='lease_expired'`. Manual `reset_claim` still works as the explicit human escape hatch. See cap #4.
- **Claim-binding** (data integrity, not permission): `release_job` and `submit_job` accept only the claimant. `reset_claim` is recovery — any key, requires reason, audit-logged.
- **First-run bootstrap**: `aq setup` (host-local CLI) creates the first Actor + first session before any key exists.
- **AQ 2.0 v1 is a trusted single-instance coordination tool.** API keys identify Actors for audit, not authorization. Not safe for multi-tenant shared services.

### Job lifecycle
States: `draft → ready → in_progress → done | failed | blocked | pending_review | cancelled`.

`update_job` is metadata-only. Transitions use explicit ops: `claim_next_job`, `release_job`, `submit_job` (4 outcomes), `reset_claim`, `review_complete`, `cancel_job`.

### One entity type
- **Pipeline** — both reusable templates (`is_template=true`) and execution runs (`is_template=false`). Template Jobs are stored in `state='ready'`, and queue operations exclude them by joining `pipelines` and requiring `p.is_template=false`. Cloning a template via `clone_pipeline` creates a new Pipeline with `cloned_from_pipeline_id` set; the cloned Jobs are also created in `state='ready'`. Templates are not versioned — change in place; clone before changing if you want to preserve the old shape. Pipelines are soft-deleted via `archive_pipeline`.

### Job edges (3 types in cap #3.5; 2 polymorphic types added in cap #9)
- `gated_on(A, B)` — A blocked from `ready` until B is `done`. Auto-resolves: when B is `done`, AQ re-evaluates Jobs gated on B; A transitions to `ready` only when **all** gates are `done` AND its Contract is complete.
- `parent_of(A, B)` — hierarchy
- `sequence_next(A, B)` — ordering only
- `job_references_decision(J, D)` — added in cap #9 with the Decisions table; requires polymorphic target.
- `job_references_learning(J, L)` — added in cap #9 with the Learnings table; requires polymorphic target.

### Contracts (inline)
Every Job carries an inline `contract JSONB NOT NULL` column. The Contract is the Definition of Done — written at `create_job` time, validated only at `submit_job` time per ADR-AQ-030. No registry, no versioning. MCP descriptions on `create_job` and `submit_job` teach the shape.

### Customization line
| Customizable per Project (governed, versioned) | System invariant (fixed) |
|---|---|
| Custom fields, labels, template Pipelines | Lifecycle states, edge types, submission payload shape, audit log shape, API key model, the four UI views, Contract structure (per ADR-AQ-030) |

### Stack
- Single repo `mmmmm-aq2.0/`
- Python: FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + Typer + FastMCP
- DB: Postgres with separate entity tables + a typed `edges` table
- Frontend: Next.js + Tailwind + shadcn/ui (read-only except 1 identity op: `create_api_key`)
- MCP mounted at `/mcp` in the same FastAPI process

---

## Capability list (ordered, dogfood at #6)

1. `[DONE] (validated 2026-04-26, merge SHA 96e158d)` Four-surface ping — one canonical operation contract round-trips through REST + CLI + MCP + UI
2. `[ ]` Actor identity, Bearer auth, and same-transaction audit log
3. `[ ]` Project, Workflow, Pipeline, and Job entities exist with full CRUD; one seeded static Workflow template ships
3.5. `[ ]` Schema consolidation — drop Workflow + Contract Profile tables, fold in AQ2-46 fix-up, regenerate parity surface for the 22-op cap-03 surface
4. `[ ]` A Job can be claimed atomically — two Actors race, exactly one wins
5. `[ ]` A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly
6. `[ ]` **Mario dogfoods one real ticket end-to-end through AQ 2.0**
7. `[ ]` Every claim and submit appends a Run Ledger entry queryable through all surfaces
8. `[ ]` Claiming a Job returns a link-only Context Packet that an Actor can follow to gather what it needs
9. `[ ]` Decisions (ADRs) and Learnings exist as graph nodes with manual capture and supersede semantics
10. `[ ]` Jobs connect through typed edges; `gated_on` auto-resolves a Job to `ready` when all gates are `done` and the Contract is complete
11. `[ ]` Four read-only UI views (Pipeline, Workflow, ADRs, Learnings) ship along with the UI-only `create_api_key` flow
12. `[ ]` AQ 2.0 installs cleanly via `pip install` / `uv pip install` with first-run migration

---

## Capabilities

### Capability #1: Four-surface ping

**Statement:** A canonical operation contract (`HealthStatus` plus `VersionInfo`) round-trips identically through REST, CLI, MCP, and the read-only UI, all rendered from one Pydantic model.

**Why this is here:** Proves the four-surface pact at zero feature cost. Without this, every later capability is at risk of surface drift. Codex correction #1 (canonical operation contracts) and #2 (CI catches drift) are validated mechanically by this capability.

**Depends on:** none.

**Scope guardrails (NOT in this capability):**
- No domain entities (Project / Workflow / Pipeline / Job) yet.
- No auth — endpoints are unauthenticated for now.
- No persistent storage — health responses are computed in-process.
- No real Run Ledger / audit log — those are scaffolded but not exercised.

**Implements ops:**
- `health_check` — `GET /healthz`, `aq health`, MCP `health_check`
- `get_version` — `GET /version`, `aq version`, MCP `get_version`

**Validation summary:** All four surfaces return identical `HealthStatus` and `VersionInfo` payloads from the same Pydantic models; OpenAPI diff against the previous build is clean; MCP schema snapshot test passes; CLI ↔ REST ↔ MCP parity test passes.

**Status:** `[DONE] (validated 2026-04-26, merge SHA 96e158d)`

---

### Capability #2: Actor identity, Bearer auth, and same-transaction audit log

**Statement:** Authenticated Actors created during first-run setup can identify themselves, list other Actors, revoke their own API keys, and read the audit log; every mutation commits its domain change and audit row in one DB transaction.

**Why this is here:** The auth and audit foundation. Codex correction #7 (sharper auth language) and #8 (audit same-transaction) demand this be solid before any workgraph mutation. Per the locked Pact, API keys are the only identity model and audit attribution is the only "permission" enforcement.

**Why this matters (human outcome):** After cap #2 ships, AQ 2.0 stops being an unauthenticated hello-world toy. Every byte of state from this point forward attributes to a named Actor. "Who did this?" has an answer for every mutation, every time. A leaked key can be killed. Trust in the queue is mechanical, not aspirational. AQ 2.0 becomes safe enough to put real work through.

**Depends on:** #1 (the four surfaces must exist).

**Scope guardrails (NOT in this capability):**
- No standalone `create_api_key` on CLI/MCP/REST — UI-only, lives in capability #11. (Cap #2 lets `create_actor` mint a one-shot plaintext key in `CreateActorResponse` because there is no UI yet — declared deviation.)
- No domain entities yet (those land in #3).
- No claim binding logic (that's #4).
- The audit log is a queryable table with one row per **mutation** (success OR business-rule denial). Reads (`whoami`, `list_actors`, `query_audit_log`, `health_check`, `get_version`) are NOT audited.
- `setup` is auditless — the founder Actor row's `created_at` IS the bootstrap evidence.
- No Web `/actors` view; no Web `/audit` view. Cap #11 owns the four read-only views (Pipelines, Workflows, ADRs, Learnings) and explicitly forbids an audit-log browser. Cap #2 Web ships only `/login`, `/logout`, and a `/whoami` panel as auth scaffolding cap #11 builds on.
- No `rotate_own_key` — users do `create_actor` (mint) plus `revoke_api_key` separately.
- No mTLS / JWT / OAuth — Bearer + plaintext-equivalent keys per single-instance trusted Pact.

**Auth model (locked, corrected from rev 1):**
- **MCP HTTP `/mcp` requires the caller's own Bearer**, identical to REST. There is no "claude-mcp-bridge" Actor. (The original AQ2-18 model proposed a bridge actor; Codex's audit caught it as a security hole — anyone reaching `/mcp` could mutate as the bridge with self-asserted identity. Dropped.)
- **MCP stdio (`aq-mcp` binary)** reads `~/.aq/config.toml` and forwards the operator's API key as Bearer to the local FastAPI process.
- **`agent_identity`** is an optional INFORMATIONAL field on every MCP tool's input schema. When provided, populates `audit_log.claimed_actor_identity` for the audit trail. Never affects authentication. Required only when an MCP host is calling on behalf of a named identity (e.g. Claude Code calling on behalf of `claude-opus-4-7`).
- **In-process service layer.** Both REST and MCP handlers call the same Python service functions. No HTTP delegation between surfaces inside the API process — that's how `claimed_actor_identity` ContextVar propagates safely.
- **All handlers `async def`.** Never sync. ContextVar safety guaranteed at the asyncio task level.

**Implements ops:**
- `setup` — `POST /setup` and `aq setup`; first-run only (advisory-locked); creates the founder Actor and a host-local API key. Auditless. Disabled after first successful run.
- `whoami` — `GET /actors/me`, `aq whoami`, MCP `get_self`. Read; not audited.
- `create_actor` — `POST /actors`, `aq actor create`, MCP `create_actor`. Mints one-shot plaintext key in response. Audited; redacted payloads.
- `list_actors` — `GET /actors`, `aq actor list`, MCP `list_actors`. Read; not audited (default scope). `?include_deactivated=true` IS audited.
- `revoke_api_key` — `DELETE /api-keys/{id}`, `aq key revoke`, MCP `revoke_api_key`. CLI/MCP/REST: own key only (403 on cross-actor; 409 on last-active-key; row-locked for race safety). Audited (success AND business-rule denial). UI variant ships in capability #11 with broader scope.
- `query_audit_log` — `GET /audit?actor=...&op=...&since=...&until=...&limit=&cursor=`, `aq audit`, MCP `query_audit_log`. Paginated; opaque cursor; SQL-injection-safe via bound parameters. Read; not audited.

**Audit semantics:**
- Reads (`whoami`, `list_actors`, `query_audit_log`, `health_check`, `get_version`) write zero audit rows.
- Mutations on success → commit domain row + audit row in one transaction.
- Mutations on business-rule denial (403, 409) → commit audit-only row with `error_code` set; no domain change.
- Mutations on unexpected exception (5xx) → roll back fully; no audit row.
- Validation errors (422) → no audit row; mutation never started.
- Setup is exempt: zero audit rows; founder row's `created_at` is the bootstrap evidence.

**Validation summary:** Run `aq setup` against an empty DB; the bootstrap returns the founder Actor + plaintext API key. Hit `aq whoami` with the key — returns the Actor row. Create a second Actor; revoke the first key — `whoami` with revoked key returns 401. Cross-actor revoke attempt returns 403 with audit row recording the denial. Query the audit log — every mutation appears, with NULL `claimed_actor_identity` for REST/CLI calls and populated values for MCP calls that carried `agent_identity`. Force a transactional failure (mock SQL error after the domain insert but before the audit insert) — confirm both rows roll back together. Validate web `/login` flow: paste API key, get redirected to `/whoami`, panel renders the Actor name; cookie is httpOnly; `document.cookie` cannot read it.

**Status:** `[ ]`

---

### Capability #3: Project, Workflow, Pipeline, and Job entities with CRUD; one seeded static Workflow template

**Statement:** All four core domain entity types exist with full CRUD ops on every surface; Contract Profiles can be discovered (list + describe); one static Workflow template (`ship-a-thing`) ships seeded so dogfooding can begin in capability #6.

**Why this is here:** The entity foundation. Without all four entities and the ability to read Contract Profiles, no Job can be created, no claim can happen, no submit can be validated. This is deliberately fat — entity schemas are the bedrock and they all need to exist together for the graph to be coherent.

**Depends on:** #2 (auth gates every mutation).

**Scope guardrails (NOT in this capability):**
- No `claim_next_job` — Jobs can be created and edited, but not claimed. Claim atomicity ships in #4.
- No `submit_job` — that's #5.
- No `instantiated_from` edges — `instantiate_pipeline` exists but its edge semantics are exercised in #10.
- No Contract Profile creation/versioning — only read (list + describe) here. Profile authoring lands in #5 with submit validation.
- No UI views — REST/CLI/MCP only at this stage.
- No automatic state transitions — `gated_on` auto-resolution lands in #10.

**Implements ops:**

Project:
- `create_project` — `POST /projects`, `aq project create`, MCP `create_project`
- `list_projects` — `GET /projects`, `aq project list`, MCP `list_projects`
- `get_project` — `GET /projects/{id}`, `aq project get`, MCP `get_project`
- `update_project` — `PATCH /projects/{id}`, `aq project update`, MCP `update_project`
- `archive_project` — `POST /projects/{id}/archive`, `aq project archive`, MCP `archive_project`

Labels (project-scoped):
- `register_label` — `POST /projects/{id}/labels`, `aq label register`, MCP `register_label`
- `attach_label` — `POST /jobs/{id}/labels`, `aq label attach`, MCP `attach_label`
- `detach_label` — `DELETE /jobs/{id}/labels/{name}`, `aq label detach`, MCP `detach_label`

**Storage shape (locked decision 2026-04-27):** Job labels are denormalized onto the Job row as `labels TEXT[] NOT NULL DEFAULT '{}'`, GIN-indexed (`USING gin (labels)`). The project-scoped `labels` registry table remains the source of truth for *which label names exist* (so `register_label` is still required before `attach_label` can succeed); the TEXT[] column on `jobs` is the query path used by `list_ready_jobs(label_filter)` and `claim_next_job(label_filter)` so those ops are O(index lookup) instead of O(join). `attach_label`/`detach_label` mutations update both the registry-junction (if implemented) and the TEXT[] cache atomically. Implementation may collapse the junction into the TEXT[] entirely if the registry validation can be enforced at write time — that decision lands in cap #3 implementation, not here.

Workflow (versioned static templates):
- `create_workflow` — `POST /workflows`, `aq workflow create`, MCP `create_workflow`
- `list_workflows` — `GET /workflows`, `aq workflow list`, MCP `list_workflows`
- `get_workflow` — `GET /workflows/{id}` (returns steps + step-edges + version), `aq workflow get`, MCP `get_workflow`
- `update_workflow` — `PATCH /workflows/{id}` (creates a new version; old version frozen), `aq workflow update`, MCP `update_workflow`
- `archive_workflow` — `POST /workflows/{id}/archive`, `aq workflow archive`, MCP `archive_workflow`

Pipeline (dynamic execution):
- `create_pipeline` — `POST /pipelines` (ad-hoc), `aq pipeline create`, MCP `create_pipeline`
- `clone_pipeline` — `POST /pipelines/{id}/clone` (copies a template/ad-hoc Pipeline and its Jobs as `ready` Jobs with inline Contract JSONB), `aq pipeline clone`, MCP `clone_pipeline`
- `list_pipelines` — `GET /pipelines`, `aq pipeline list`, MCP `list_pipelines`
- `get_pipeline` — `GET /pipelines/{id}`, `aq pipeline get`, MCP `get_pipeline`
- `update_pipeline` — `PATCH /pipelines/{id}`, `aq pipeline update`, MCP `update_pipeline`

Job (CRUD only — no claim/submit yet):
- `create_job` — `POST /jobs` (binds to a Pipeline and requires inline `contract` JSONB), `aq job create`, MCP `create_job`
- `list_jobs` — `GET /jobs`, `aq job list`, MCP `list_jobs`
- `get_job` — `GET /jobs/{id}`, `aq job get`, MCP `get_job`
- `update_job` — `PATCH /jobs/{id}` (metadata only: title, description, label attachments; rejects state writes), `aq job update`, MCP `update_job`
- `comment_on_job` — `POST /jobs/{id}/comments`, `aq job comment`, MCP `comment_on_job`
- `cancel_job` — `POST /jobs/{id}/cancel`, `aq job cancel`, MCP `cancel_job`
- `list_ready_jobs` — `GET /jobs/ready?label=area:web&label=area:api&project=...`, `aq job list-ready`, MCP `list_ready_jobs`. Read-only; **never audited** (matches cap #2 reads-not-audited lock). Returns a paginated, FIFO-ordered set of Jobs in state `ready` whose attached labels (per the existing `register_label`/`attach_label` model above) are a superset of the supplied `label_filter`, and excludes template / archived Pipelines by joining `pipelines` and requiring `p.is_template=false AND p.archived_at IS NULL`. Same filter semantics that `claim_next_job` (cap #4) uses, so an MCP-connected agent can preview the queue before deciding to claim. Limit `<= 100`, opaque cursor for paging. Jobs carry inline `contract` JSONB per ADR-AQ-030.

Contract Profile discovery was cancelled in AQ2-50 and removed by cap #3.5. Jobs carry inline `contract` JSONB; there is no profile registry in v1.

Plus: a database migration that seeds one static template Pipeline (`ship-a-thing` with three ready Jobs, each with a non-empty ADR-AQ-030-shaped inline Contract).

**Validation summary:** Create a Project, attach two labels, register a custom label, clone the seeded `ship-a-thing` template Pipeline (verify three cloned Jobs in `ready`, each with copied inline Contract JSONB and no template Jobs leaking into `list_ready_jobs`), create a fourth ad-hoc Job in the Pipeline with inline Contract JSONB, list everything, update the Job's title, comment on it, cancel one of the cloned Jobs. Every mutation appears in the audit log except read-only list/get operations.

**Note (2026-04-28):** Stories 3.5 (AQ2-44) and 3.7 (AQ2-46) shipped Workflow versioning and `instantiate_pipeline` as originally spec'd, then were unwound by cap #3.5. The corresponding tables (`workflows`, `workflow_steps`, `contract_profiles`), ops (5 workflow ops, `instantiate_pipeline`, 2 profile discovery ops), and FK constraints (composite FK on `jobs (pipeline_id, project_id)`) no longer exist after cap #3.5 ships. Story 3.11 (AQ2-50, Contract Profile discovery) was cancelled. Final cap #3 op count after cap #3.5: **22 ops** (was 28). `get_job` / `get_pipeline` / `get_project` ship with empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays so cap #9 can wire data without breaking the response shape (per [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Decision 7).

**Status:** `[ ]`

---

### Capability #3.5: Schema consolidation (Workflow→Pipeline collapse + Contract Profile drop + AQ2-46 fix-up)

**Statement:** After cap #3.5 ships, the v1 schema reflects the final v1 design — one Pipeline entity (templates and runs), no Workflow tables, no Contract Profile tables, inline `contract JSONB` on every Job, two new generic edge types for D&L cross-references (`job_references_decision`, `job_references_learning`), and a `clone_pipeline` op replacing `instantiate_pipeline`. AQ2-46's mypy/ruff fix-up is folded in. All four surfaces (REST + CLI + MCP + UI) parity-test the new op set. The seeded `ship-a-thing` data lives as a template Pipeline, not a Workflow.

**Why this is here:** Per [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md), six structural decisions reshape v1. Decisions 2 (Workflows→Pipelines) and 3 (drop Contract Profiles) require unwinding code that already shipped on `aq2-cap-03` through AQ2-46. Cap #3.5 makes the schema match the v1 thesis before more code lands on top of it. Per the "lets get this right now" decision, schema cruft is unacceptable — dead Workflow / Contract Profile / `instantiated_from` columns introduce false patterns for agents to learn from.

**Depends on:** AQ2-39 epic and AQ2-40 through AQ2-46 are MERGED on `aq2-cap-03`. AQ2-46's mypy/ruff fix-up landed at `784125a` (claude-approved 2026-04-28). AQ2-47 has NOT been claimed.

**Scope guardrails (NOT in this capability):**
- No new ops beyond `clone_pipeline` and `archive_pipeline`. The other op-count changes are deletions, not additions.
- No source-code work on cap #4+ ops yet (those reference the post-cap-#3.5 schema but aren't built here).
- No PR yet — cap #3.5 squashes into the cap #3 PR at C2.

**Implements (5 stories, one commit each):**

- **Story 3.5.0 — AQ2-46 mypy/ruff fix-up.** Already complete (Codex commit `784125a`, claude approval `aea6f81d-...`). Story exists in Plane for evidence + per-commit gate discipline.
- **Story 3.5.1 — Schema migration `0005_cap0305_schema_consolidation`.** One Alembic revision drops `workflow_steps`, `workflows`, `contract_profiles`; drops columns `jobs.instantiated_from_step_id`, `jobs.contract_profile_id`, `pipelines.instantiated_from_workflow_id`, `pipelines.instantiated_from_workflow_version`; drops constraints `jobs_pipeline_project_composite_fk`, `pipelines_id_project_id_uniq`; drops `instantiated_from` from edge_type enum; adds columns `pipelines.is_template`, `pipelines.cloned_from_pipeline_id`, `pipelines.archived_at`, `jobs.contract`; adds edge type values `job_references_decision`, `job_references_learning`. Seed migration: read existing `ship-a-thing` v1 + 3 steps, create one `pipelines` row with `is_template=true`, create 3 `jobs` rows in `state='ready'` with non-empty ADR-AQ-030-shaped inline Contract JSONB. Idempotent on fresh DB and on existing dev DB. Template Jobs are excluded from queue operations by the `pipelines` join and `p.is_template=false`, not by Job state.
- **Story 3.5.2 — Service + route + CLI + MCP changes.** Remove all Workflow + Contract Profile + instantiate_pipeline source files, tests, CLI commands, MCP tools, SQLAlchemy models, Pydantic models, and route registrations. Add `clone_pipeline` + `archive_pipeline` ops on REST + CLI + MCP. Add `apps/api/tests/test_pipeline_template_and_clone.py` covering `is_template` filter, clone semantics, archive. Add a clone atomicity test.
- **Story 3.5.3 — OpenAPI + MCP snapshot regeneration + parity test updates.** Regenerate `tests/parity/openapi.snapshot.json` and `tests/parity/mcp_schema.snapshot.json`. Update `tests/parity/test_four_surface_parity.py` — remove `-k workflow`, `-k profile`, `-k instantiate` parameterized cases; add `-k clone`, `-k template` cases. Update `tests/parity/mcp_harness.py`. Verify Web TypeScript types regenerate cleanly.
- **Story 3.5.4 — Evidence pack + cap #3.5 C-checkpoint.** Run full Docker test matrix, mypy strict, ruff. Verify post-migration DB shape matches the spec. Push branch tip. Stop. Post comprehensive evidence on the cap #3.5 epic. Wait for claude audit. Wait for Ghost approval.

**Validation summary:** After Story 3.5.4 ships, run `\dt` against the dev DB — `workflows`, `workflow_steps`, `contract_profiles` all gone. Query `SELECT column_name FROM information_schema.columns WHERE table_name='jobs' AND column_name IN ('instantiated_from_step_id','contract_profile_id')` — empty. Query `SELECT column_name FROM information_schema.columns WHERE table_name='jobs' AND column_name='contract'` — returns `contract`. Query `SELECT column_name FROM information_schema.columns WHERE table_name='pipelines' AND column_name IN ('is_template','cloned_from_pipeline_id','archived_at')` — returns all three. Query `SELECT count(*) FROM pipelines WHERE is_template=true AND name='ship-a-thing'` — returns 1. Run `pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity` — all pass. `mypy --strict apps/api/src/aq_api/` and `ruff check apps/api apps/cli` clean.

**Op-count math:** Starting cap-03 op count: 28. After cap #3.5: −5 Workflow ops − 1 `instantiate_pipeline` − 2 profile ops + 1 `clone_pipeline` + 1 `archive_pipeline` = **−6 ops, final cap-03 = 22**.

**Status:** `[ ]`

---

### Capability #4: A Job can be claimed atomically

**Statement:** Two Actors race to claim the same `ready` Job; exactly one wins (Job transitions to `in_progress` with the claimant set), the other gets a 409 Conflict; claimants can release; any Actor can `reset_claim` a stuck `in_progress` Job with a reason.

**Why this is here:** Proves "pull, do not push" (LinkedIn Post 10) is actually atomic at the database level. Without this, two agents can step on the same work and the loop is broken. Codex correction #3 (split `release_job` / `reset_claim`) is validated here.

**Depends on:** #3 (Jobs must exist before they can be claimed).

**Scope guardrails (NOT in this capability):**
- No `submit_job` — claim works, but the only way to exit `in_progress` here is `release_job` or `reset_claim`. Submit ships in #5.
- No `gated_on` resolution — claim works on any `ready` Job; the gating logic that promotes `draft → ready` lands in #10. For now, Jobs go directly to `ready` on creation.
- ~~No claim filtering by `required_capabilities` — there are no capabilities. Claim is FIFO over `ready` Jobs (optionally filtered by Project).~~ **Superseded 2026-04-27** by label-based filtering (see `label_filter` on `claim_next_job` below). **Why superseded:** the original lock conflated "no agent-capability registry" with "no work routing at all." We still don't have an agent-capability registry — agents do not declare profiles to AQ. But Jobs already carry labels (cap #3), and labels are the right axis for an MCP-connected agent to scope the work it picks up (e.g., `area:web` agents grab web work, `area:api` agents grab API work). This is data-driven routing, not a capability table.
- No agent-capability registry. Agents do not register profiles with AQ. Routing is entirely on the caller side: the agent passes `label_filter` to `claim_next_job` and is responsible for its own scope discipline. AQ enforces the filter atomically; AQ does not reason about which agent "should" claim what.
- No `parallel_safe` file-conflict flag. Two `ready` Jobs with no `gated_on` edge between them are eligible to be claimed concurrently by different Actors even if their work touches the same file. Application-level conflict (merge collisions, lock files) is the agents' problem to coordinate, not AQ's.

**Implements ops:**
- `claim_next_job` — `POST /jobs/claim`, `aq job claim`, MCP `claim_next_job`. Uses `SELECT ... FOR UPDATE SKIP LOCKED` semantics in a single transaction that also inserts the audit row. Accepts an optional `label_filter` (list of label names; the SKIP LOCKED query adds `AND labels @> :label_filter` against the GIN-indexed `jobs.labels` TEXT[] column locked in cap #3). The query joins `pipelines` and requires `p.is_template=false AND p.archived_at IS NULL`, so cap #3.5 template Jobs in `ready` never enter the work queue. FIFO ordering preserved within the filter scope. The claim audit row records the resolved `label_filter` so we can answer "why did this agent get this Job?" later.
- `release_job` — `POST /jobs/{id}/release`, `aq job release`, MCP `release_job`. Claimant only. Job returns to `ready`. Sets `claim_heartbeat_at` to NULL.
- `reset_claim` — `POST /jobs/{id}/reset-claim` with required `reason`, `aq job reset-claim`, MCP `reset_claim`. Any key. Job returns to `ready`. Manual escape hatch — stays unchanged.
- `heartbeat_job` — `POST /jobs/{id}/heartbeat`, `aq job heartbeat`, MCP `heartbeat_job`. Claimant only. Refreshes `claim_heartbeat_at` to `now()`. **Successful heartbeats do NOT write audit rows** (lease maintenance, not business history; the Job's `claim_heartbeat_at` column stores the only state AQ needs). Cross-claimant attempts → 403 with `error_code='heartbeat_forbidden'`, audit row recorded. Heartbeat on a non-`in_progress` Job → 409 with `error_code='job_not_in_progress'`, audit row recorded. Heartbeat on missing Job → 404 with `error_code='job_not_found'`, audit row recorded. This is a documented deviation from cap #2's "every mutation audits" rule, locked in `capability-04-plan.md` Locked Decision 5.

**Heartbeat lease (locked decision 2026-04-27):**
- New column on `jobs`: `claim_heartbeat_at TIMESTAMPTZ NULL`. Set by `claim_next_job` on every successful claim. Refreshed by `heartbeat_job`. Cleared by `release_job`, `reset_claim`, and the auto-release sweep below.
- Configuration: `AQ_CLAIM_LEASE_SECONDS` (default `900` = 15 minutes). Required at boot via `pydantic-settings`. Range-checked to `[60, 86400]` to prevent foot-guns at either extreme.
- Sweep cadence: `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` (default `60`, range `[5, 3600]`). Server polls for stale claims at this cadence. Actual release latency for a dead claimant is bounded by `AQ_CLAIM_LEASE_SECONDS + AQ_CLAIM_SWEEP_INTERVAL_SECONDS`.
- Auto-release sweep: an in-process asyncio coroutine on the API process re-flips Jobs from `in_progress` to `ready` when `now() - claim_heartbeat_at > :lease`. Each auto-release writes an audit row with `op='claim_auto_release'`, `target_kind='job'`, `target_id=job_id`, `authenticated_actor_id` set to the reserved `aq-system-sweeper` actor (created idempotently at app startup and seeded by migration `0006_cap04_indexes_and_system_actor`), `error_code='lease_expired'`, and `request_payload` containing `previous_claimant_actor_id`, `stale_claim_heartbeat_at`, and `lease_seconds` for forensic continuity. Per-Job atomicity: each Job's reset and its audit row commit in one transaction; batch failures leave the invariant intact. Manual `reset_claim` stays — explicit human escape hatch unchanged, used when the auto-release window is too long for a known-dead agent.

**MCP richness (required from this capability forward — sets the pattern for every later cap):**

Cap #1 ships only `health_check` + `get_version`, which are trivially safe and self-explanatory. Starting at this capability, every MCP op MUST layer in MCP-spec features beyond the basic input/output schema:

1. **Server-level instructions** (one-time, on the AQ MCP server itself, not per-tool). FastMCP 2.14.7 exposes this through `FastMCP(..., instructions=...)` / `.instructions`; set that block so it surfaces to the agent the moment the server connects:
   - "Pass `agent_identity` (the API key alias) on every call. AQ does not infer it."
   - "Errors come back as structured objects: `{error_code, rule_violated, details}`. Do NOT retry on `rule_violated` — it indicates a fixable client mistake (wrong claimant, wrong state, missing field), not a transient failure."
   - "After a successful `claim_next_job`: the response includes a Context Packet stub (forward-compat with cap #8 — currently empty `previous_jobs[]` and `next_job_id: null`). Read the Job's inline `contract` field for the DoD, call `heartbeat_job` every ~30 seconds while working, and call `submit_job` (cap #5) when done. For now, use `release_job` if you can't complete the work."
2. **Tool annotations** per [MCP spec](https://modelcontextprotocol.io/specification/) — set explicitly on every tool, not defaulted:
   - `claim_next_job`, `release_job`, `reset_claim`, `heartbeat_job` → `destructiveHint: true`, `idempotentHint: false` (state-changing). `heartbeat_job` is technically idempotent on the row's `claim_heartbeat_at` value if called in the same instant, but the lease semantics are state-changing from the agent's perspective, so it ships as `idempotentHint: false`.
   - `get_job`, `list_jobs`, `list_ready_jobs`, `whoami` (and every read-only op) → `readOnlyHint: true`.
   - These let hosts like Claude Code skip the approval prompt for read-only ops and gate destructive ops behind explicit consent.
3. **Tool descriptions** — auto-derived from the Pydantic model docstrings + a per-op "why-to-use / when-to-use" line authored in the MCP tool definition (NOT in the model). Description must answer: *what the tool does, what state it requires, what it returns, what to call next*.
4. **Output content bundling** — `claim_next_job` returns a multi-part MCP content list:
   - The Job itself (structured Pydantic dump as JSON content).
   - The Context Packet object (cap #8 link-only nav) inline so the agent doesn't need a second round-trip.
   - A natural-language `text` block: "You claimed AQ-123. Required next: read the Job's inline `contract` field; call `heartbeat_job` every ~30s; `submit_job` ships in cap #5. The Packet's `previous_jobs[]` and `next_job_id` populate when cap #10's `sequence_next` edges land."
5. **Tool input-schema field descriptions** — every Pydantic field used as an MCP tool argument carries a docstring; FastMCP auto-derives JSON Schema `description`s from those docstrings. No second source of truth.

**Resources and Prompts** layer in at later caps where they actually have content to serve:
- **Resources** (URI-addressable on-demand content): land in cap #11 (Pipeline / ADR / Learning resources by URI).
- **Prompts** (server-defined slash-command templates): land in cap #6 dogfood — one prompt template `/aq-claim-and-work` wrapping the standard claim → read-packet → submit pattern.

**Validation summary:** Create a Project, Pipeline, two `ready` Jobs. Two CLI clients (different keys) call `aq job claim` simultaneously on the same Project; assert one gets a Job ID and the other gets the second Job (or a `409 no_ready_job` if there's only one). Re-run with one Job — exactly one client gets it, the other gets `409 no_ready_job`. From the winner, call `aq job release` — Job returns to `ready`. Re-claim, then from a different key call `aq job reset-claim --reason "claimant crashed"` — Job returns to `ready` and the audit log shows the reason. Run the race 50× to confirm no double-claim. **Label filter checks:** create five Jobs with mixed `area:web` / `area:api` labels; `aq job list-ready --project <uuid> --label area:web` returns only the web-labeled subset in FIFO order; `aq job claim --project <uuid> --label area:api` skips the web ones even when they're at the head of FIFO; the resulting claim audit row records `request_payload.label_filter = ["area:api"]`. **Heartbeat lease checks:** claim a Job, set `AQ_CLAIM_LEASE_SECONDS=60`, wait past the lease without a heartbeat, observe the auto-release sweep flips the Job back to `ready` with audit row `op='claim_auto_release'` `error_code='lease_expired'`; another claim, send `aq job heartbeat` every ~30s, confirm the Job stays `in_progress` and `claim_heartbeat_at` advances without success audit rows; cross-claimant heartbeat (a different actor's key) returns 403 `error_code='heartbeat_forbidden'` with audit row recorded. **Plus MCP richness checks:** call MCP `tools/list`, assert `claim_next_job` and `heartbeat_job` have `destructiveHint=true` and `readOnlyHint=false`; call `get_job` and `list_ready_jobs` and assert `readOnlyHint=true`; call MCP server `instructions` endpoint, assert it returns the agent_identity + error-shape rules; call `claim_next_job` from a real MCP client and assert the response is a multi-part content list including a Packet block and a next-step text hint.

**Status:** `[ ]`

---

### Capability #5: A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly

**Statement:** A claimant calls `submit_job` with `outcome ∈ {done, pending_review, failed, blocked}` and a structured payload; AQ shape-validates the payload against the Job's inline `contract` JSONB per ADR-AQ-030 (no profile registry — dropped in cap-3.5 per `plan-update-2026-04-28.md` Decision 3), checks state transition validity, commits the new state and audit row in one transaction, and writes an audit row queryable as a "run" via cap #7's `list_runs`/`get_run` (no separate Run Ledger table per `plan-update-2026-04-28.md` Decision 1). Pending-review Jobs can be transitioned to `done` or `failed` by any key via `review_complete`. Per `plan-update-2026-04-28.md` Decision 4, `submit_job` accepts inline `decisions_made[]` and `learnings[]` arrays; non-empty entries create rows in the `decisions` and `learnings` tables attached to the submitting Job, in the same transaction. Cap #9 ships the standalone D&L ops + the inheritance lookups in `get_*` responses.

**Why this is here:** The submission boundary is moat #5 — Contracts are schemas, not CI. Codex correction #4 (`update_job` is metadata only — transitions are explicit ops) is validated by this capability. Without this, a Job can be created and claimed but never finished — the loop doesn't close.

**Depends on:** #4 (claim must exist before submit).

**Scope guardrails (NOT in this capability):**
- No DoD-runner that *executes* tests. Validation is shape-only: outcome-specific required fields, declared DoD ids matching `dod_results[]`, terminal status per required DoD item for `outcome=done`, and evidence presence for passed DoDs.
- No `gated_on` auto-resolution — `done` updates the state but does not trigger downstream Job promotion. That's #10.
- No automatic Learning capture beyond explicit `learnings[]` entries submitted by the caller — standalone D&L ops ship in #9.
- No Run Ledger query — audit rows are queryable through cap #7's `list_runs` / `get_run`.
- No Contract Profile registry, profile authoring, or profile versioning. Jobs carry inline `contract` JSONB from cap #3.5.

**Implements ops:**
- `submit_job` — `POST /jobs/{id}/submit` with `outcome` and payload, `aq job submit`, MCP `submit_job`. Claimant only. Outcome-specific required fields:
  - `done` — closeout payload (`dod_results`, `commands_run`, `verification_summary`, `files_changed`, `risks_or_deviations`, `handoff`, `decisions_made[]`, `learnings[]`)
  - `pending_review` — `submitted_for_review` notes plus the same submission shape
  - `failed` — `failure_reason` plus partial submission
  - `blocked` — `gated_on_job_id` + `blocker_reason` (inserts one `job_edges(edge_type='gated_on')` row in the same transaction; cap #10 owns auto-resolution)
- `review_complete` — `POST /jobs/{id}/review-complete` with `final_outcome ∈ {done, failed}`, `aq job review-complete`, MCP `review_complete`. Any key. Only valid when Job is in `pending_review`.

**Note (2026-04-28):** Per `plan-update-2026-04-28.md` Decisions 1, 3, and 4: no Run Ledger table; no Contract Profile registry; inline D&L creation at submit time. Cap-5's locked shape is canonical.

**MCP richness (extends the cap #4 pattern):**

Continue the MCP-richness pattern established in cap #4. Specifically for this capability:

1. **`submit_job` annotations** — `destructiveHint: true`, `idempotentHint: false` (terminal or near-terminal state transition). Description must spell out the four outcomes and the per-outcome required fields, and point callers to the Job's inline `contract` field.
2. **`submit_job` output bundling** — on success returns multi-part content: the updated Job dump + a `text` block with the next-step hint. There is no audit-row reference block; cap #7's `list_runs` / `get_run` queries audit rows by target and timestamp.
3. **`review_complete` annotations** — `destructiveHint: true`, `idempotentHint: false`; description names the any-actor review rule and the `pending_review`-only state constraint.

**Validation summary:** Create a Job with inline Contract JSONB, claim it, submit with `outcome=done` and a complete payload — Job transitions to `done`, audit row written, and non-empty `decisions_made[]` / `learnings[]` entries are visible in the `decisions` / `learnings` tables. Submit a different Job with an invalid payload (missing required field) — submit returns 422 and Job stays `in_progress`. Submit one with `outcome=pending_review` — Job lands in `pending_review`. From a different key, call `aq job review-complete --final-outcome done` — Job is `done`. Submit one with `outcome=failed` — Job is `failed` with audit `error_code=NULL`. Submit one with `outcome=blocked` and `gated_on_job_id=<other_id>` — Job is `blocked` and a `gated_on` edge is inserted. Try to submit a `done` Job again — rejected as terminal. Try to submit as a non-claimant — 403. Run cap-5 race tests: 50 concurrent submit attempts produce exactly one winner, and sweep-vs-submit atomicity covers both interleavings with no partial D&L or audit state.

**Status:** `[ ]`

---

### Capability #6: Mario dogfoods one real ticket end-to-end through AQ 2.0

**Statement:** Mario picks one real piece of work he was going to track in Plane (or anywhere else) and runs it through AQ 2.0 instead — creates a Project, instantiates a Pipeline from the seeded `ship-a-thing` Workflow, creates 3–5 real Jobs, claims them as Claude Code (or another agent) using a UI-minted API key (UI-mint flow stubbed for this capability via direct DB insert if the UI isn't ready yet), works the Jobs externally, submits with valid Contracts, and lands at least one `done` Job through actual real work.

**Why this is here:** **The riskiest assumption from the Brief.** AQ 1.0 sprawled because no real work passed through it until Phase 6.6's lab test — and that wasn't real work, it was a fixture. AQ 2.0 forces the real-work check at capability #6, before any Run Ledger / Context Packet / Decisions / UI work is built. If something is missing or broken for real use, we find it now, not at capability #11.

**Depends on:** #5 (the loop must close before real work can pass through).

**Scope guardrails (NOT in this capability):**
- **No new ops are implemented in this capability.** It is a *use* capability, not a feature capability. The architecture already supports real work after #5; this capability validates that claim.
- No UI yet — Mario uses CLI + MCP for this. The UI-mint API key flow is not required; for capability #6, host-local CLI can mint a key (a one-time concession until #11 ships).
- No retroactive backfill of existing Plane tickets. Pick fresh real work.
- No promise of a beautiful UX. The point is to find the rough edges.

**Implements ops:** none new. This capability composes ops from #1–#5.

**Validation summary:** Mario writes a brief one-pager describing the real work he ran through AQ 2.0: which Project, which Workflow / Pipeline, what each Job was, what was painful, what surprised him. The pager goes into `D:\mmmmm\mmmmm-aq2.0\plans\v2-rebuild\dogfood-1.md`. The DB at the end of the run shows a real `done` Job with a real Contract-valid submission and a real audit trail. If anything broke, it goes into the pager and becomes a fix-first item before capability #7 starts.

**Status:** `[ ]`

---

### Capability #7: Run queries (read-only views over audit log)

**Statement:** `list_runs` and `get_run` ops query the existing `audit_log` table via a partial index, surfacing claim and terminal-transition rows (`claim_next_job`, `submit_job`, `review_complete`) as "runs." No new tables. The audit log is the database of record; cap #7 just exposes a queryable view over it.

**Why this is here:** Coordinators need to ask "what did Claude do this week?" / "what runs have we had on Job X?" The audit log already captures every claim and submit with full payload, actor, timestamp, and `error_code` — adding a separate `run_ledger` table would be a denormalization with no new information. Per [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) Decision 1, cap #7 collapses into audit_log queries.

**Depends on:** #5 (submit must work before the audit log has anything queryable as a "run").

**Scope guardrails (NOT in this capability):**
- No `run_ledger` table. The audit_log IS the ledger.
- No analytics, no rollup queries, no aggregation. Run queries return rows.
- No filtering by Workflow version (Workflows don't exist after cap #3.5).

**Implements ops:**
- `list_runs` — `GET /runs?job=...&actor=...&since=...&outcome=...`, `aq run list`, MCP `list_runs`. SELECT over `audit_log` filtered by `op IN ('claim_next_job', 'submit_job', 'review_complete')`. Performance comes from a partial index: `CREATE INDEX audit_log_runs_idx ON audit_log (created_at DESC, target_id) WHERE op IN ('claim_next_job', 'submit_job', 'review_complete') AND error_code IS NULL`.
- `get_run` — `GET /runs/{id}`, `aq run get`, MCP `get_run`. Single audit_log row fetch by primary key.

Both successes and business-rule denials live in the same audit_log table — distinguished by whether `error_code` is set.

**Validation summary:** Run the full claim → submit cycle on three Jobs. Query `aq run list --since yesterday` — three rows. Query `aq run list --actor claude-runner-1` — only that actor's runs. Query `aq run list --outcome done` — only `done` outcomes. Try `aq run list --job <id>` — full claim+submit history for that Job. Verify `EXPLAIN ANALYZE` on `list_runs` shows `Index Scan using audit_log_runs_idx`.

**Status:** `[ ]`

---

### Capability #8: Claiming a Job returns a link-only Context Packet

**Statement:** A successful `claim_next_job` returns a Context Packet object containing pointers (IDs, not content): `project_id`, `pipeline_id`, `previous_jobs[]` (last 2 in the Pipeline's Sequence), `current_job_id`, and `next_job_id`. The same packet is reachable post-claim via `get_packet`. The Actor follows links to read what it needs via existing `get_*` ops; Contract content lives inline on the Job.

**Why this is here:** The packet is *navigation, not content*. Codex correction (link-only design) and the user's spec ("read pj-1, pl-1, prev 2 jobs, current, next") are implemented here. AQ 1.0's Phase 3 Context Compiler built a content-bundling packet that became its own bottleneck; AQ 2.0 deliberately doesn't.

**Depends on:** #4 (claim must exist), #5 (Contracts must exist), #3 (entities must be linkable).

**Scope guardrails (NOT in this capability):**
- No content bundling. The packet does not include the Project description, Workstream goal, prior-job summaries, or Contract field text. The Actor follows links.
- No retrieval, no embeddings, no FTS. Graph traversal only — and only along the Pipeline's Sequence edges.
- No automatic redaction (since there's no content to redact).
- The packet returns structural pointers (ID lists) to Decisions and Learnings: those attached directly to the Job, those inherited from the parent Pipeline, and those inherited from the parent Project. Each pointer carries an `inherited_from` field with values `direct`, `pipeline`, or `project` so the agent can reason about scope. No relevance ranking. No content. The Actor follows links via `get_decision` and `get_learning` to retrieve content; the inheritance metadata exists so the agent knows where each attachment came from before deciding whether to fetch it. (Per [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Decision 7.)

**Implements ops:**
- `get_packet` — `GET /jobs/{id}/packet`, `aq packet`, MCP `get_packet`. Returns the link-only navigation object. `claim_next_job` (already in #4) is extended to include the same packet in its response payload so the Actor doesn't need a second round-trip.

**Validation summary:** Create or clone a Pipeline with three Jobs in a Sequence (Job A, B, C). Claim Job B. The claim response contains a packet pointing to Project, Pipeline, Job A as the only previous, Job B as current, and Job C as next. From the same key, call `aq packet B` — same payload. Confirm no Project description, Workflow goal text, or Job descriptions are in the packet — only IDs and stable identifiers. Have an agent (Claude Code) follow each link via `get_*` ops and verify it can reconstruct the full context independently, reading Contract content from the Job's inline `contract` field.

**Status:** `[ ]`

---

### Capability #9: Decisions and Learnings exist as graph nodes with manual capture and supersede semantics

**Statement:** Decisions (ADRs) and Learnings are first-class graph nodes with their own CRUD ops; Decisions can be superseded (creating a `supersedes` edge); Learnings can be edited; both are linkable to Jobs and Pipelines through generic graph edges (introduced fully in #10) but the basic CRUD lands here so dogfood and review have a place to write what they learn.

**Why this is here:** The LinkedIn canon (Posts 7, 8, 16) leans hard on Decisions and Learnings as the durable artifacts that make AQ different from a queue. They have to exist as real nodes — not as Markdown files. AQ 1.0's Phase 2.5 over-engineered this with promotion / dedup / similarity ranking; AQ 2.0 ships only manual capture and supersede.

**Depends on:** #6 (dogfood validates that the loop works; now we add the durable-artifact entities).

**Scope guardrails (NOT in this capability):**
- No 3-tier Learning promotion (job → project → global). Single scope, manual.
- No similarity ranking, no dedup, no auto-merge.
- No Learning auto-draft from run trace.
- No FTS / pgvector / trgm search. List + get only.
- No automatic Learning surfacing in future Context Packets — that's #10's gated_on/edge-aware machinery.

**Implements ops:**

Decision:
- `create_decision` — `POST /decisions`, `aq decision create`, MCP `create_decision`
- `list_decisions` — `GET /decisions`, `aq decision list`, MCP `list_decisions`
- `get_decision` — `GET /decisions/{id}`, `aq decision get`, MCP `get_decision`
- `supersede_decision` — `POST /decisions/{id}/supersede` (creates a typed `supersedes` edge), `aq decision supersede`, MCP `supersede_decision`

Learning:
- `submit_learning` — `POST /learnings`, `aq learning submit`, MCP `submit_learning`
- `list_learnings` — `GET /learnings?scope=project|job`, `aq learning list`, MCP `list_learnings`
- `get_learning` — `GET /learnings/{id}`, `aq learning get`, MCP `get_learning`
- `edit_learning` — `PATCH /learnings/{id}`, `aq learning edit`, MCP `edit_learning`

**Validation summary:** Create three Decisions, supersede the second with a third — confirm the `supersedes` edge exists and the older Decision's status is `Superseded`. Submit four Learnings tied to the dogfood Job from #6. List Learnings filtered by Project — returns four. Edit one — version increments, audit row written. Confirm Decisions appear in the graph via `query_graph_neighborhood` (which is part of #10's machinery — defer test if needed, or add a stub graph query for this capability).

**Note (2026-04-28 — response-shape extension on cap #3 ops):** Per [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Decision 7, when this capability ships, the response shapes of `get_job`, `get_pipeline`, and `get_project` (already in cap #3) are extended to include `decisions` and `learnings` objects with `direct: [...]` and `inherited: [...]` arrays. Direct entries are full Decision or Learning records attached to the entity; inherited entries are full records attached up the chain (Pipeline's parent Project for `get_pipeline`; both parent Pipeline and parent Project for `get_job`). Each inherited entry carries an `inherited_from` field (`pipeline` or `project`) and the source entity's ID so the agent can reason about scope. `get_project` returns only direct attachments; the Project is the top of the inheritance chain. **Cap #3 ships these ops with empty arrays from day one** so cap #9 can wire data without breaking the response shape — a forward-compatible extension, not a breaking change.

**Status:** `[ ]`

---

### Capability #10: Jobs connect through typed edges; `gated_on` auto-resolves; the graph is queryable

**Statement:** Three Job-to-Job edge types are persisted and queryable: `gated_on`, `parent_of`, `sequence_next`. When a Job transitions to `done`, AQ re-evaluates all Jobs with unsatisfied `gated_on(_, that_job)` and transitions them from `draft` to `ready` if and only if all their `gated_on` dependencies are `done` and their Contract is complete. The graph is **queryable** — three traversal ops (`list_descendants`, `list_ancestors`, `query_graph_neighborhood`) expose multi-hop reachability with cycle detection and bounded depth. Two additional polymorphic edge types — `job_references_decision`, `job_references_learning` — land in cap #9 alongside the Decision and Learning tables they target (the `job_edges` shape needs a polymorphic target since Decisions and Learnings are not Jobs).

**Why this is here:** Codex pushback: "we have to be able to connect Jobs." Without typed edges and auto-resolution, AQ is just a queue with linked-list ordering. Without traversal, the graph is structure that exists but doesn't answer questions; the "work provenance graph" thesis is oversold. This capability makes the graph honest.

**Depends on:** #3 (Jobs must exist), #5 (state transitions must be wired so `done` triggers the resolver), #9 (edge ops are general; Decisions also use them).

**Scope guardrails (NOT in this capability):**
- No additional edge types beyond the five (`gated_on`, `parent_of`, `sequence_next`, `job_references_decision`, `job_references_learning`). Custom edge types are not user-customizable per the locked customization line.
- No materialized adjacency caches. Traversal queries hit the live `edges` table via Postgres recursive CTEs.
- No `find_path(node_a, node_b)` op. Path-finding is deferred to v1.1+ unless cap #6 dogfood reveals a use case.
- No graph visualization view. UI views ship in #11 and don't include graph viz; visualization is v1.1+.
- No graph-shaped permissions. Edges are universally readable per the Pact's "every Actor sees every Job" rule.

**Implements ops:**

Edge persistence:
- `link_jobs` — `POST /edges` with `{source_id, target_id, edge_type}`, `aq edge link`, MCP `link_jobs`
- `unlink_jobs` — `DELETE /edges/{source}/{target}/{type}`, `aq edge unlink`, MCP `unlink_jobs`
- `list_job_edges` — `GET /jobs/{id}/edges?direction=in|out|both`, `aq job edges`, MCP `list_job_edges`

Graph traversal (per [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Decision 7):
- `list_descendants` — `GET /graph/{node_type}/{node_id}/descendants?edge_types=...&max_depth=...`, `aq graph descendants`, MCP `list_descendants`. Returns `{nodes, edges, truncated, cycle_detected}`. Default depth 10, server hard cap 50. Recursive CTE with cycle detection.
- `list_ancestors` — symmetric inverse of `list_descendants`. Same shape.
- `query_graph_neighborhood` — `GET /graph/neighborhood?start_id=...&start_type=...&depth=...&edge_types=...&direction=...&node_type_filter=...`. Returns local subgraph at depth 1, 2, or 3. Hard cap 500 nodes; returns `error_code='neighborhood_too_large'` with `count` field if exceeded.

Plus: the `submit_job` handler from #5 is extended with the gated-on resolver — when a Job transitions to `done`, run a query for every Job with an unsatisfied `gated_on` edge to it; for each, check whether all gates are satisfied AND the Contract is complete (per ADR-AQ-030 minimum_claimable_invariants); if both, transition `draft → ready` in the same transaction. (Single-hop resolution at submit time stays unchanged; the new traversal ops query the same edge structure asynchronously without affecting the resolver.)

**Validation summary:** Create three Jobs A, B, C. Link `gated_on(B, A)` and `gated_on(C, A)`. Confirm B and C are in `draft`. Submit A with `outcome=done` — confirm B and C now in `ready`. Repeat with B incomplete-Contract — submit A → only C transitions to `ready`, B stays `draft` because its Contract is incomplete. List edges on a Job in both directions. Try to link a self-edge — rejected. Try `parent_of` on Jobs in different Projects — allowed (cross-project parent is rare but valid).

**Plus traversal checks:** Build a chain of 6 Jobs with `gated_on(B,A), gated_on(C,B), gated_on(D,C), gated_on(E,D), gated_on(F,E)`. Call `list_descendants(A)` — returns 5 nodes (B through F) with depth values 1 through 5; `truncated=false`, `cycle_detected=false`. Call `list_ancestors(F)` — returns 5 nodes (E through A). Force a cycle via `link_jobs(F, A, gated_on)` — surfaces as `cycle_detected=true` on subsequent traversals (acceptable). Call `query_graph_neighborhood(B, depth=2, edge_types=['gated_on'])` — returns 5 nodes within 2 hops. Call with `depth=3, node_type_filter=['decision']` — only Decision nodes return. Force `neighborhood_too_large` against a deeply-connected node — verify `error_code` and `count` field. **Performance canary:** build a Pipeline with 50 Jobs and a 10-deep `gated_on` chain; assert all traversal ops complete in <100ms.

**Status:** `[ ]`

---

### Capability #11: Read-only UI views ship along with the UI-only `create_api_key` flow

**Statement:** The Next.js + Tailwind + shadcn/ui app is online with four read-only views (Pipelines, Workflows, ADRs, Learnings) that consume existing read ops via REST; the only mutation in the UI is `create_api_key` (and `revoke_api_key` extended to allow revoking any key from the UI), exposed through an authenticated browser session.

**Why this is here:** The UI is a window per moat #4 — but it has to exist for non-CLI users to be able to *use* AQ 2.0. By gating UI delivery to capability #11 (after dogfood, after edges, after the durable artifacts), we avoid AQ 1.0's Phase 7 trap of building UI before the loop was real.

**Depends on:** #1–#10. The UI is the integration surface; everything must work first.

**Scope guardrails (NOT in this capability):**
- **No write UI for workgraph state.** Zero buttons that POST/PATCH/DELETE to `/projects`, `/pipelines`, `/workflows`, `/jobs`, `/decisions`, `/learnings`, etc. Every "I want to create / change this" UX shows the equivalent CLI command instead, with a copy-button.
- No graph visualization view, no analytics dashboard, no audit-log browser. The audit log is queryable via CLI/MCP/REST only.
- No emergency pause or kill-switch on Jobs from the UI.
- No bulk operations.
- The UI is responsive enough to be usable on a laptop browser, but no mobile / tablet polish.

**Implements ops:**
- `create_api_key` — UI only. `POST /api-keys` reachable only through a logged-in browser session (cookie auth derived from email/password login). Returns the key value once at creation; later requests return only the key ID and prefix. CLI/MCP/REST never expose this op.

Plus: the UI itself — Next.js app with four read-only views that call existing REST endpoints. UI types are generated from OpenAPI to enforce parity per Codex correction #1.

**Validation summary:** Boot the UI against a populated DB (post-dogfood). Log in as Mario via email/password — session cookie set. Hit each of the four views; each renders without errors. Pipeline view shows the dogfood Pipeline from #6 with its Jobs in correct states. Workflow view shows the seeded `ship-a-thing` Workflow with its versions. ADRs view lists Decisions from #9. Learnings view lists Learnings from #9 with filter by Project. Click "create API key" — modal asks for a name; submit; key value returned once and shown in the modal with a copy-to-clipboard button; subsequent fetches don't include the key value. Try `POST /api-keys` from `curl` (no UI session) — returns 404 (op doesn't exist outside UI).

**Note (2026-04-28 — read-only forever):** Per [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) Decision 6, the UI is read-only forever. The single exception is `create_api_key` (this capability), which is the sole UI-only mutation in v1, v1.1, and v1.2. No UI mutations for Decisions, Learnings, webhook subscriptions when v1.1 ships them, or any future configuration — agents do all input via MCP. SSO is dropped from v1.2 (proposed v1.3+ if real demand); email/password + cookie session as cap #11 ships continues. A read-only audit log query UI is added in v1.2 (this capability explicitly forbids an audit-log browser; v1.2 reverses that for read-only viewing only).

**Status:** `[ ]`

---

### Capability #12: AQ 2.0 installs cleanly via `pip install` / `uv pip install` with first-run migration

**Statement:** A user with Postgres available can run `pip install agenticqueue` (or `uv pip install`), set a `DATABASE_URL` env var, run `aq setup`, and reach a working AQ 2.0 instance — backend + UI dev server + first Actor + first key — in under five minutes from a clean machine.

**Why this is here:** Open-source / clean install path is in the Brief's success criteria. Without this, customers #2 (solo builders) and #4 (OSS contributors) can't adopt. AQ 1.0 had Phase 10 deployment as a dedicated phase; AQ 2.0 collapses it into one capability that ships *after* everything else works.

**Depends on:** #1–#11.

**Scope guardrails (NOT in this capability):**
- No Docker image yet — that's v1.1.
- No multi-tenant deployment. Single-instance only per the auth disclaimer.
- No managed cloud. Self-host only.
- No automated upgrade migration between versions. Alembic migrations on first-run only; upgrade UX is a v1.1 capability.
- No SBOM / Sigstore / SLSA / OIDC — those AQ 1.0 Phase 9 items remain dropped.

**Implements ops:** none new. `setup` was already in #2. This capability adds packaging, distribution, first-run UX polish, and the install docs.

**Validation summary:** From a fresh Ubuntu container with Postgres but no AQ install: `uv pip install agenticqueue`, `export DATABASE_URL=postgres://...`, `aq setup` — outputs the host-local API key and writes it to `~/.aq/config.toml`. `aq health` returns 200 from the running FastAPI process. Open `http://localhost:8000` — UI loads, login page shows. Log in with the credentials `aq setup` printed. All four views render. Time from `pip install` to seeing the Pipeline view: under 5 minutes on a normal laptop.

**Status:** `[ ]`

---

## Coverage check (every op covered exactly once) — rev 4 (2026-04-28)

| Op | Capability |
|---|---|
| `health_check` | #1 |
| `get_version` | #1 |
| `setup` | #2 |
| `whoami` | #2 |
| `create_actor` | #2 |
| `list_actors` | #2 |
| `revoke_api_key` | #2 (CLI/MCP/REST self-only) + extended in #11 (UI any-key) |
| `query_audit_log` | #2 |
| `create_project` | #3 |
| `list_projects` | #3 |
| `get_project` | #3 (response extended in #9) |
| `update_project` | #3 |
| `archive_project` | #3 |
| `register_label` | #3 |
| `attach_label` | #3 |
| `detach_label` | #3 |
| `create_pipeline` | #3 |
| `list_pipelines` | #3 |
| `get_pipeline` | #3 (response extended in #9) |
| `update_pipeline` | #3 |
| `archive_pipeline` | #3 (added in cap #3.5) |
| `clone_pipeline` | #3 (added in cap #3.5; replaces `instantiate_pipeline`) |
| `create_job` | #3 (takes inline `contract` JSONB per plan-update Decision 3) |
| `list_jobs` | #3 |
| `get_job` | #3 (response extended in #9) |
| `update_job` | #3 |
| `comment_on_job` | #3 |
| `list_job_comments` | #3 |
| `cancel_job` | #3 |
| `list_ready_jobs` | #3 |
| `claim_next_job` | #4 |
| `release_job` | #4 |
| `reset_claim` | #4 |
| `heartbeat_job` | #4 |
| `submit_job` | #5 |
| `review_complete` | #5 |
| `list_runs` | #7 (queries audit_log via partial index per plan-update Decision 1) |
| `get_run` | #7 (queries audit_log) |
| `get_packet` | #8 (extended with inheritance metadata) |
| `create_decision` | #9 |
| `list_decisions` | #9 |
| `get_decision` | #9 |
| `supersede_decision` | #9 |
| `submit_learning` | #9 |
| `list_learnings` | #9 |
| `get_learning` | #9 |
| `edit_learning` | #9 |
| `link_jobs` | #10 |
| `unlink_jobs` | #10 |
| `list_job_edges` | #10 |
| `list_descendants` | #10 (NEW per Decision 7) |
| `list_ancestors` | #10 (NEW per Decision 7) |
| `query_graph_neighborhood` | #10 (NEW per Decision 7) |
| `create_api_key` | #11 (UI only) |

**Op count: 54.** (was 56 in rev 3.) Net change from rev 3:
- Removed: 5 Workflow ops, `instantiate_pipeline`, 2 Contract Profile discovery ops (AQ2-50 cancelled), 2 Contract Profile authoring ops = −10
- Added in cap #3.5: `archive_pipeline`, `clone_pipeline` = +2
- Already in rev 3 (counting reconciliation): `list_job_comments`, `list_ready_jobs` (cap #3); `heartbeat_job` (cap #4)
- Added in graph addendum (cap #10): `list_descendants`, `list_ancestors`, `query_graph_neighborhood` = +3
- Net: 56 − 10 + 2 + 0 (rev-3 reconciliation, already counted) + 3 = 51, plus AQ2-49 (`list_ready_jobs`) + AQ2-48 (`list_job_comments`) + cap-4 `heartbeat_job` were already line items in the rev-3 table = the table above totals 54.

Per-capability count: #1=2, #2=6, #3=22 (was 28), #4=4 (added `heartbeat_job`), #5=2 (was 4 — removed Profile authoring), #6=0 new, #7=2, #8=1, #9=8, #10=6 (was 3 — added 3 traversal), #11=1, #12=0 new. Sum: 2+6+22+4+2+0+2+1+8+6+1+0 = **54**. The table above is canonical (one row per op = 54). **The "51" count from rev-4 banner section was a miscalculation** — the correct final v1 op count after rev 4 is 54 (cap-3 drops from 28 to 22, cap-5 drops from 4 to 2, cap-10 grows from 3 to 6, cap-4 grows from 3 to 4 by adding `heartbeat_job`, AQ2-50 cancelled). Cap #3 ships 22 ops final after cap #3.5 ships.

Capabilities #6 and #12 deliberately implement no new ops — they are use / packaging capabilities that compose existing ops. Every other op appears under exactly one capability.

---

## Backlog (post-v1 deferred items)

Items that are out of scope for v1 (caps #1–#12) but are explicit deferrals — not silent drops. Each item names the capability/story that deferred it, the source ADR or rationale, and the proposed v1.1+ landing point. When v1 ships, this section is the seed for the v1.1 Brief.

| Item | Source | Reason for deferral | Proposed landing |
|---|---|---|---|
| **MCP SSE transport** | Cap #1 / Story 1.4 (AQ2-6) | ADR-AQ-021 lists three MCP transports (stdio, streamable HTTP, SSE). v1 ships stdio (`aq-mcp` binary) + streamable HTTP at `/mcp`. SSE is older spec, mostly redundant with streamable HTTP for our use cases, and adding it now would inflate Story 1.8's parity test surface for marginal gain. Declared as a deviation in cap #1 submission per ADR-AQ-030. | v1.1 — add `aq-mcp-sse` mount + parity test 2b (SSE schema snapshot). One story. |
| **Docker image publishing** | Cap #12 | Cap #12 ships `pip install` / `uv pip install` only. No Docker image push to a registry (Docker Hub / GHCR). The `docker-compose.yml` from cap #1 builds locally; no published artifact. | v1.1 — GHCR push from `build.yml`, tag = git SHA + `latest`. |
| **Multi-tenant deployment** | Cap #2 (auth disclaimer) | v1 is "trusted single-instance coordination tool." API keys identify Actors for audit, not authorization. Multi-tenant changes the threat model: per-tenant key scoping, row-level security in Postgres, isolation tests. | v1.1+ — capability of its own. Likely 2–3 capabilities (key scoping → RLS → isolation tests). |
| **Automated upgrade migrations between versions** | Cap #12 | First-run Alembic migration only; no v0→v1 upgrade UX. Not a problem until there's a real install base. | v1.1 — `aq upgrade` CLI command + Alembic upgrade path. |
| **Custom field add/extend on Contract shapes** | Cap #5 | Cap #3.5 dropped the Contract Profile registry. v1 Jobs carry inline `contract` JSONB, and cap #5 validates only the locked submit payload shape. No governed profile-edit surface ships in v1. | v1.1+ — revisit if dogfood needs reusable governed Contract templates; design a new registry from current requirements instead of reviving the cancelled profile ops. |
| **Multi-hop dependency analysis** | Cap #10 | v1 only does single-hop `gated_on` resolution at submit time. No "show me everything that depends on X transitively" tools. | v1.1 — graph traversal ops (`list_descendants`, `list_ancestors`, `find_cycles`). |
| **Graph visualization UI view** | Cap #11 | The four read-only views (Pipelines, Workflows, ADRs, Learnings) ship; no graph viz of edges. | v1.1+ — usually a separate workstream; needs a layout engine decision (Cytoscape vs D3 vs hand-rolled SVG). |
| **Audit-log browser UI** | Cap #11 | Audit log is queryable via CLI/MCP/REST only in v1. | v1.1 — read-only audit view. |
| **3-tier Learning promotion (job → project → global)** | Cap #9 | v1 ships single-scope Learnings (manual capture + supersede). No promotion ladder. | v1.1+ — would need similarity ranking + dedup (also deferred). |
| **Learning similarity ranking + dedup + auto-merge** | Cap #9 | v1 ships manual capture only. No FTS, no pgvector, no trgm search on Learnings. | v1.1+ — own workstream; depends on which retrieval stack we standardize. |
| **Learning auto-draft from run trace** | Cap #9 | v1 Learnings are hand-written. No "AQ proposes a Learning from this run." | v1.1+. |
| **Bulk operations in UI** | Cap #11 | No bulk Project archive, bulk Job cancel, etc. | v1.1+ if real demand. |
| **Mobile / tablet UI polish** | Cap #11 | Laptop browser only. | v1.1+. |
| **Matrix CI (multi Python / Node version)** | Cap #1 / Story 1.9 | Single-config CI: Python 3.12 + Node 20. | v1.1 — add `strategy: matrix` once we have a real reason. |
| **SBOM / Sigstore / SLSA / OIDC** | Cap #12 | AQ1 Phase 9 items, deliberately dropped from v1 scope. | v1.2+ — only if customer asks. |
| **Webhook subscriptions (outbound)** | [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) Decision 5 | v1 is pull-only via MCP polling. Webhook-based agent wake-up violates "pull, do not push." Outbound notifications to external systems (Slack, GitHub, Notion) deferred. | v1.1 — outbound notifications only; agent wake-up never. |
| **SSO (OAuth / SAML / Google login)** | [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) Decision 6 | UI continues to use email/password + cookie session. SSO adds OAuth/SAML provider integration complexity not justified at v1 scale. | v1.3+ if real demand. |
| **Audit-log query UI** | [`plan-update-2026-04-28.md`](plan-update-2026-04-28.md) Decision 6 | Cap #11 explicitly forbids an audit-log browser; audit log is queryable via CLI/MCP/REST only in v1. | v1.2 — read-only audit view. |
| **Materialized adjacency caches** | [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Section 1 | Recursive CTEs handle v1 scale (hundreds of Jobs) in single-digit ms. Materialization is an optimization that's only justified once a real workload is hitting limits. | v1.1+ when traversal queries become a performance concern. |
| **`find_path(node_a, node_b)` op** | [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Section 1 | Path-finding is powerful but unproven need at v1. Cap #6 dogfood will reveal whether it's wanted. | v1.1+ if dogfood reveals a use case. |
| **Cycle prevention at `link_jobs` time** | [`plan-update-2026-04-28-graph.md`](plan-update-2026-04-28-graph.md) Section 5 | v1 surfaces cycles as `cycle_detected: true` on traversal. Preventing them at link time is stricter but adds overhead to every link operation. | v1.2+ if cycles become a real operational problem. |
| **Re-introduce strict project_id consistency at DB level (composite FK)** | Cap #3.5 | F-P1-rev2-7 was unwound in cap #3.5 because the Workflow→Pipeline collapse made the composite FK awkward to maintain. Project_id consistency is now enforced at the application layer. If consistency drift is observed in dogfood, add the composite FK back. | v1.1+ if a real bug surfaces. |
| **`submit_job` Contract requires `commit_sha` field by schema** | Dogfooding observation 2026-04-28 (claude) — cap #5 | Cap #5's `submit_job` payload validates against the inline Contract per ADR-AQ-030. Today, an agent CAN submit without a commit_sha if the Contract doesn't require one. In dogfood (this session), the linkage between Plane tickets, commit SHAs, and audit comments is established by claude copy-pasting IDs by hand. Making `commit_sha` a required Contract field for code-producing Jobs would mechanically enforce the linkage. | v1 — small spec refinement to cap #5's `submit_job` Contract template; all v1 contracts that wrap code work require `commit_sha`. Docs-only and research Contracts can opt out via Contract type. |
| **Audit verdicts as first-class Decisions; "AQ audits AQ"** | Dogfooding observation 2026-04-28 (claude) — cap #9 | When claude rejects a story (e.g., AQ2-46 lint regression on 2026-04-28), the verdict is durable judgment that belongs in the Decisions table (cap #9). Today it lives as a Plane comment with an opaque UUID handle. Once cap #9 ships, the standing process should be: every per-story audit verdict creates a `decision` linked to the Job via `job_references_decision` edge, not just a comment. Plus: AQ2 itself becomes the source of truth for AQ2 development — Plane retires once cap #11 UI ships. | v1.1 — process change after cap #9 + cap #11 ship. No code change in cap #9; the change is in claude's audit workflow. |
| **`claim_next_job` MCP polling replaces verbal Codex handoffs** | Dogfooding observation 2026-04-28 (claude) — cap #4 | Today every Codex-next-ticket handoff is a chat copy-paste from claude → Mario → Codex. Once cap #4 ships, Codex's MCP integration should poll `claim_next_job` directly on the AQ2 stack — the contract describes what to do, the Job has the SHA range to work in, and the chat relay drops out. This is the "agents work the queue" thesis made literal. | v1 — already in cap #4 by design. **Process change** lands when cap #4 ships: switch Codex from chat relay to MCP poll. |
| **Orchestrator-side heartbeat visibility (`list_jobs?state=in_progress` at session start)** | Dogfooding observation 2026-04-28 (claude) — cap #4 | When Codex is mid-implementation, claude has no signal that work is in flight other than waiting for Mario's next message. Cap #4's heartbeat lease + `list_ready_jobs` give claude the data; using them at session-start is a workflow change. | v1 — workflow change for claude when cap #4 ships. Consider adding a session-start checklist to AGENTS.md / CLAUDE.md. |
| **Spec evolution as first-class — versioned capabilities.md with diff-aware authority resolution** | Dogfooding observation 2026-04-28 (claude) — meta | Every plan change today writes a new `plan-update-YYYY-MM-DD-*.md` file at the top of the plans folder. The most recent file wins, but conflicts between the file and the older `capabilities.md` are resolved manually with rev-banners pointing at the canonical source. This is a workaround for not having spec versioning as a feature of AQ. A capability that treats `capabilities.md` (and capability-NN-plan.md) as graph nodes with version pins, supersede edges, and authority resolution would let Decisions reference the exact spec revision they were made against. | v1.2+ — only if dogfood reveals enough churn to justify the abstraction. Could land as a generalization of cap #9's supersede semantics over plan documents. |

When something else gets deferred during execution, add a row here with the same shape (Source / Reason / Proposed landing).

**Dogfooding meta-insight (logged 2026-04-28):** AQ2 is succeeding as dogfood specifically because we keep finding things AQ2 should solve that we're currently solving manually. Every "Mario, can you forward this to Codex?" is an arrow pointing at a missing capability. The cap #3.5 collapse came from exactly this kind of observation — Workflows + Contract Profiles were over-engineered relative to how work actually flowed. Future-claude / future-Codex: when you notice yourself doing something by hand that AQ2 *should* be doing, file a backlog row above with `Source: Dogfooding observation YYYY-MM-DD`. The pattern matters more than the specific item — that's how v1.1 figures out what to ship.

---

## Log

- 2026-04-26 — Capability #1 Four-surface ping validated and squash-merged to main at `96e158d`.
- 2026-04-26 — Pre-plan approved by Ghost. Capability #1 marked `[ACTIVE]`. Plane epic AQ2-1 + stories AQ2-3..AQ2-11 created. capability-01-plan.md drafted with ADR-AQ-030-shaped DoDs.
- 2026-04-26 — Codex review pass: timestamp parity loosened to "valid + recent" (not byte-equal); MCP transports clarified per ADR-AQ-021 (stdio via `aq-mcp` + streamable HTTP at `/mcp`; SSE deferred); `gen:types` reads committed OpenAPI snapshot (not live HTTP); validation script split into `.sh` + `.ps1`; Stories 1.8/1.9 swapped (Parity tests before CI workflows so CI references real test files); Web UI proxies to API via `app/api/health` + `app/api/version` route handlers (no CORS); scaffold expanded with lockfiles + workspace + framework configs.
- 2026-04-26 — Codex audit pass 2: (P1) folded ticket-body corrections into canonical bodies for AQ2-2..AQ2-11; (P1) added Plane `blocked_by` edges so dependency order is enforced by tooling not prose; (P1) fixed Story 1.7 Docker build context to repo root with `dockerfile:` paths and specified concrete healthcheck commands; (P2) reconciled `capabilities.md` cap-#1 status from `[ACTIVE]` back to `[ ]` until AQ2-3 transitions to `todo` (matches Plane truth).
