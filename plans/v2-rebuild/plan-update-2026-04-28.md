# Plan update — 2026-04-28

**Audience:** Codex (implementer), Claude (planner/auditor), Ghost (oversight).
**Scope:** Decisions made in the 2026-04-28 strategy conversation that change the v1 plan as captured in `capabilities.md` (current rev: post-cap-#1) and the live Plane ticket bodies for AQ2-39 through AQ2-51.
**Authority:** This file supersedes the conflicting sections of `capabilities.md` and the conflicting sections of AQ2-39, AQ2-47, AQ2-49, AQ2-50, AQ2-51 ticket bodies. Where they disagree, this file wins.
**Read order for agents:** Read this file first, then read `capabilities.md` and the unchanged Plane tickets (AQ2-40 through AQ2-46, AQ2-48).
**Naming:** All references to "AQ" / "AQ 2.0" / "AgenticQueue" stay as-is in this document. Renaming is deferred until Ghost makes the final call.

---

## Section 0 — Validated state of cap #3 (pulled from Plane via MCP, 2026-04-28)

| Story | Plane ID | State | Branch tip | Implementation status |
|---|---|---|---|---|
| 3.1 Schema migration | AQ2-40 | DONE | d0df338 | 9 tables created. 4 contract profiles + ship-a-thing workflow seeded. |
| 3.2 Pydantic models | AQ2-41 | DONE | a475cf2 | All cap-03 contract models live. |
| 3.3 Project ops | AQ2-42 | DONE | 90c36b5 | 5 ops, parity verified. |
| 3.4 Labels | AQ2-43 | DONE | 3b21099 | TEXT[] cache + GIN. **C1 PASSED.** |
| 3.5 Workflow versioning | AQ2-44 | **DONE** | f644277 | All 5 ops live: create/list/get/update/archive. Slug families, version-supersede chains, family-level archive, stale-version 409s. **Approved by Claude.** |
| 3.6 Pipeline ops (ad-hoc) | AQ2-45 | **DONE** | b80a43f | 4 ops live: create/list/get/update. project_id immutable. **Approved by Claude.** |
| 3.7 instantiate_pipeline | AQ2-46 | DONE | 355dc8c | Snapshot semantics live. Composite FK enforced. **Approved with mypy/ruff fix-up pending** (2 mypy strict errors + 1 line-length violation — see Section 7). |
| 3.8 Job CRUD | AQ2-47 | **BACKLOG** | — | **NOT YET CLAIMED. This is the live decision point.** |
| 3.9 Comments + cancel | AQ2-48 | BACKLOG | — | Unaffected by this update. |
| 3.10 list_ready_jobs | AQ2-49 | BACKLOG | — | Minor edits per Section 5. |
| 3.11 Profile discovery | AQ2-50 | BACKLOG | — | **CANCELLED.** See Decision 3. |
| 3.12 Parity + C2 | AQ2-51 | BACKLOG | — | Substantial edits per Section 5. |

The C1 checkpoint passed on 2026-04-27. C2 has not yet been reached. We are between checkpoints, with AQ2-47 not yet claimed. **This is the cleanest possible insertion point for the schema correction.**

---

## TL;DR

Six structural decisions, one mid-ship procedure:

1. **Run Ledger collapses into the audit log.** Cap #7 dies as a separate capability. `list_runs` / `get_run` become thin queries over the audit log via a partial index.
2. **Workflows collapse into Pipelines.** One entity type. `is_template` boolean + `clone_pipeline` op replaces `instantiate_pipeline`.
3. **Contract Profiles are dropped entirely.** No registry, no versioning, no seeded profiles. Contracts are inline JSONB on each Job.
4. **Decisions and Learnings change shape.** Captured at submit AND standalone. Attached to ONE entity (Job/Pipeline/Project) — attachment IS the scope. Two new generic edge types for manual linkage.
5. **Webhooks move to v1.1, not v1.** Agent coordination is pull-only via MCP polling. Outbound webhooks (Slack, GitHub, Notion) are v1.1.
6. **UI stays read-only forever.** No SSO in v1.2. Audit log query UI is added in v1.2 (read-only).

**Procedure (Path Y1-Insert):** Cap #3 work pauses immediately after AQ2-46. A new **cap #3.5 — Schema consolidation** epic is inserted between AQ2-46 and AQ2-47. Cap #3.5 unwinds Workflows, Contract Profiles, and the dead schema columns from cap #3, and folds in AQ2-46's pending mypy/ruff fix-up. After cap #3.5 ships and is approved, cap #3 resumes with modified AQ2-47, AQ2-48, AQ2-49, and a substantially-rewritten AQ2-51. AQ2-50 is cancelled.

---

## Decision 1 — Run Ledger collapses into audit log

### What changes

Cap #7 is removed as a standalone capability. The `run_ledger` table does not exist. The two ops `list_runs` and `get_run` survive but are reimplemented as thin queries over the existing `audit_log` table.

### Why

The audit log is the database of record. It already captures every claim, every submit, every review_complete with full payload, actor, timestamp, and error_code. A separate `run_ledger` table would be a denormalization of data we already have, with no new information.

### Implementation

`list_runs(job_id, actor_id, since, until, outcome)` becomes a SELECT over `audit_log` filtered by `op IN ('claim_next_job', 'submit_job', 'review_complete')`. Performance comes from a partial index, not a materialized view:

```sql
CREATE INDEX audit_log_runs_idx
  ON audit_log (created_at DESC, target_id)
  WHERE op IN ('claim_next_job', 'submit_job', 'review_complete')
    AND error_code IS NULL;
```

`get_run(run_id)` becomes a single audit_log row fetch by primary key. Both successes and business-rule denials live in the same audit_log table — distinguished by whether `error_code` is set.

### Plan deltas

| File | Change |
|---|---|
| `capabilities.md` cap #7 | Replace entire capability with: "Cap #7 — `list_runs` and `get_run` ops query the audit log via partial index. No new tables." Title becomes "Run queries (read-only views over audit log)." |
| `capabilities.md` cap #5 description | Remove "emits a Run Ledger entry" — submit emits an audit row that is *queryable as* a run, but there is no separate Run Ledger emission. |

### Risk to existing work

None. Cap #7 hasn't started.

---

## Decision 2 — Workflows collapse into Pipelines

### What changes

The two-entity-type model collapses into one entity: `Pipeline`. Pipelines gain two columns:

- `is_template BOOLEAN NOT NULL DEFAULT false`
- `cloned_from_pipeline_id UUID NULL REFERENCES pipelines(id)`

Versioning of templates is dropped. If you want to keep an old version, clone the template before changing it.

The `instantiated_from` edge type and the `instantiated_from_step_id` direct FK are both removed. Provenance lives in the `cloned_from_pipeline_id` column.

### Op changes

| Old op | New op | Notes |
|---|---|---|
| `create_workflow` | (removed) | `create_pipeline(is_template=true)` |
| `list_workflows` | (removed) | `list_pipelines(is_template=true)` |
| `get_workflow` | (removed) | `get_pipeline` |
| `update_workflow` | (removed) | `update_pipeline` |
| `archive_workflow` | (removed) | `archive_pipeline` (new — adds soft-delete to pipelines) |
| `instantiate_pipeline` | `clone_pipeline(source_id)` | Copies all Jobs from source as `draft` Jobs in the new pipeline. |

Net op count change: removing 6 ops (5 workflow + 1 instantiate), adding 2 ops (clone_pipeline, archive_pipeline) = **−4 ops**.

### Why this matters for AQ2-44 / AQ2-46

AQ2-44 already shipped Workflow versioning (slug families, supersede chains, family archive, NOT NULL FK to contract_profiles). AQ2-46 already shipped instantiate_pipeline with composite FK enforcement. Both work correctly. Both ship a contract that this update removes.

The `workflows`, `workflow_steps`, `contract_profiles` tables exist in the schema (per AQ2-40 migration `0004_cap03_entities`), have seeded data, and have FK relationships into pipelines and jobs. The cleanup is non-trivial. **It is the bulk of cap #3.5's scope** — see Section 4.

---

## Decision 3 — Contract Profiles dropped entirely

### What changes

The Contract Profile concept is removed from v1:

- No `contract_profiles` table.
- No `contract_profile_versions` table (was implied by F-P0-4 — never built).
- No seeded profiles (`coding-task`, `bug-fix`, `docs-task`, `research-decision` — gone).
- No ops: `list_contract_profiles`, `describe_contract_profile`, `register_contract_profile`, `version_contract_profile` are all removed.
- No `default_contract_profile_id` column on `workflow_steps` (the F-P0-rev2-1 lock is unwound — but the table itself goes away in Decision 2).
- No `contract_profile_id` column on `jobs`.
- No MCP Resources URI namespace `aq://policies/contract-profile/{name}`.

Replacement: every Job carries an inline `contract JSONB NOT NULL` column. The Contract is the Definition of Done — a JSONB document on the Job row itself, written at Job creation time, validated only at `submit_job` time against the structure ADR-AQ-030 specifies.

### Why

Profiles were solving a problem we don't have. In Plane, every ticket had its DoD written into the ticket body — there was no profile registry, no versioning, no seeded library. Inline contracts match how Mario actually planned work. ADR-AQ-030 is preserved — its structure becomes the JSON schema of the inline `contract` field.

### Op changes

| Old op | Status |
|---|---|
| `list_contract_profiles` | Removed |
| `describe_contract_profile` | Removed |
| `register_contract_profile` | Removed (was cap #5) |
| `version_contract_profile` | Removed (was cap #5) |

`create_job` gains a required `contract JSONB` argument.

Net op count change: **−4 ops**.

### Why this matters for AQ2-44 / AQ2-50

AQ2-44 already enforced `default_contract_profile_id NOT NULL` on every workflow_steps row (DOD-AQ2-S5-05). Cap #3.5 unwinds that constraint as part of dropping the workflow_steps table.

AQ2-50 was about to expose `list_contract_profiles` and `describe_contract_profile` as read-only ops. **AQ2-50 is cancelled.** No code, no tests, no work. The seeded profile rows in the contract_profiles table sit unused until cap #3.5 drops the table.

---

## Decision 4 — Decisions and Learnings change shape

### What changes

Decisions and Learnings remain first-class graph nodes. The capture and scoping model changes:

**Capture model:**

1. At submit time — the inline Contract on every Job requires `decisions_made[]` and `learnings[]` arrays (empty is valid). Non-empty arrays create Decision and Learning nodes inline in `submit_job`'s transaction, attached to the Job.
2. Standalone, anytime — `create_decision` and `submit_learning` continue to exist as standalone ops, callable at any time by any actor with a valid key.

**Scoping model:**

A Decision or Learning is attached to **exactly one** entity: a Job, a Pipeline, or a Project. **The attachment is the scope.** No separate `scope` field. No global scope.

**Retrieval model:**

`get_job`, `get_pipeline`, `get_project` are extended to return their attached Decisions and Learnings inline. The Context Packet (cap #8) returns structural pointers only — no relevance computation, no filtering.

**Manual linkage model (new edge types):**

Two new edge types are added for manual cross-references:

- `job_references_decision(job_id, decision_id)`
- `job_references_learning(job_id, learning_id)`

Created via the existing `link_jobs` op. Total Job-related edge type count is now **5**: `gated_on`, `parent_of`, `sequence_next`, `job_references_decision`, `job_references_learning`. `instantiated_from` is removed (Decision 2). The `supersedes` edge on Decisions is preserved per cap #9.

### Why

Decisions/Learnings are durable artifacts. Forcing capture as a separate after-the-fact step meant they were never written. Attaching at submit time matches how Mario actually wrote them in Plane. Scope as a separate field duplicated information already in attachment.

### This is cap #5 + cap #9 work, not cap #3 work

Decision 4 affects cap #5 (`submit_job` Contract validation) and cap #9 (Decision/Learning ops). It does not affect cap #3 or cap #3.5. Codex implements Decision 4 when cap #5 and cap #9 start, not now.

---

## Decision 5 — Webhooks move to v1.1

### What changes

Webhook subscriptions are removed from v1. Agent coordination in v1 is pull-only via MCP polling.

Codex Automations and Claude Routines both poll the AQ MCP server every 15 minutes (or whatever interval the agent's owner configures), call `claim_next_job` with a label filter, do the work, and submit back. No webhook fan-out from AQ to wake agents.

### Why

1. Pull is the Pact. "Pull, do not push" is foundational. Webhook-based wake is a thin form of orchestration that violates it.
2. Pull is symmetric. Codex Automations cannot be inbound-webhooked (confirmed by OpenAI Support, 2026-04-15). Claude Routines can. Symmetric pull lets every agent type integrate the same way.
3. The dogfood demo doesn't need webhooks. 15-minute polling is fine for cap-shaped work.

### What v1.1 webhooks cover

When webhooks ship in v1.1, they're outbound notifications to external systems (Slack, GitHub, Notion) — not agent wake-up. The Claude Routines `/fire`-style endpoint can be added as a special outbound subscription type at that point, marked as a latency optimization, not the primary integration path.

### Backlog updates

| Item | Status |
|---|---|
| Webhook subscriptions | New backlog row. Proposed v1.1. |

---

## Decision 6 — UI stays read-only

### What changes

The UI is read-only forever. The single exception in v1 is `create_api_key` (cap #11), which is the sole UI-only mutation. Beyond that, no UI mutations are added in v1, v1.1, or v1.2.

Agents do all input via MCP. This includes Decisions, Learnings, webhook subscriptions when v1.1 ships them, and any future configuration.

**SSO is dropped from v1.2.** The UI continues to use email/password + cookie session as cap #11 ships.

**Audit log query UI is added in v1.2 (read-only).** Cap #11 explicitly forbids an audit-log browser; v1.2 reverses that for read-only viewing only.

### Backlog updates

| Item | Status |
|---|---|
| SSO | Dropped from v1.2. Proposed v1.3+ if real demand. |
| Audit log query UI | New backlog row. Proposed v1.2. |

---

## Section 4 — Cap #3.5: Schema consolidation (NEW EPIC)

**Insertion point:** Between cap #3 (in flight, paused at AQ2-46 done) and cap #4 (atomic claim).
**Rationale:** Per the "lets get this right now" decision, schema cruft is unacceptable. The dead Workflow / Contract Profile / instantiated_from columns introduce false patterns for agents to learn from. Cap #3.5 makes the schema match the v1 thesis before more code lands on top of it.

### Cap #3.5 statement

After cap #3.5 ships, the v1 schema reflects the final v1 design: one Pipeline entity (templates and runs), no Workflow tables, no Contract Profile tables, inline `contract JSONB` on every Job, two new generic edge types for D&L cross-references, and a `clone_pipeline` op replacing `instantiate_pipeline`. AQ2-46's mypy/ruff fix-up is folded in. All four surfaces (REST + CLI + MCP + UI) parity-test the new op set. The seeded `ship-a-thing` data lives as a template Pipeline, not a Workflow.

### Cap #3.5 dependencies

- AQ2-39 epic and AQ2-40 through AQ2-46 are MERGED on `aq2-cap-03`.
- AQ2-46's mypy/ruff fix-up has NOT been independently merged. Cap #3.5 absorbs it.
- AQ2-47 has NOT been claimed.

### Cap #3.5 stories (proposed — Codex implements one per commit, Claude audits each)

**Story 3.5.0 — Fold in AQ2-46 mypy/ruff fix-up**
- Fix `apps/api/src/aq_api/services/instantiate.py:23` — `state="ready"` typed via `cast(JobState, "ready")` or explicit Literal annotation.
- Fix `apps/api/src/aq_api/services/instantiate.py:42` — `cast(DbWorkflow | None, await session.scalar(...))` or explicit annotation.
- Fix `apps/api/tests/test_instantiate_pipeline.py:422` — split assertion to fit 88 chars.
- Run `mypy --strict apps/api/src/aq_api/services/instantiate.py` and `ruff check` over the affected files.
- One commit. This story exists so the fix-up doesn't get lost in the bigger schema migration.

**Story 3.5.1 — Schema migration: drop Workflow tables, drop Contract Profile tables, drop dead columns, add new columns**

One Alembic migration revision `0005_cap0305_schema_consolidation` doing:

```
-- Drop tables (in dependency order)
DROP TABLE workflow_steps;
DROP TABLE workflows;
DROP TABLE contract_profiles;

-- Drop columns from jobs
ALTER TABLE jobs DROP COLUMN instantiated_from_step_id;
ALTER TABLE jobs DROP COLUMN contract_profile_id;

-- Drop columns from pipelines (Workflow snapshot fields)
ALTER TABLE pipelines DROP COLUMN instantiated_from_workflow_id;
ALTER TABLE pipelines DROP COLUMN instantiated_from_workflow_version;

-- Drop the composite FK and supporting unique constraint (F-P1-rev2-7 unwind)
ALTER TABLE jobs DROP CONSTRAINT jobs_pipeline_project_composite_fk;
ALTER TABLE pipelines DROP CONSTRAINT pipelines_id_project_id_uniq;

-- Drop instantiated_from value from edge_type enum
-- (Postgres enum value drop is via type swap or value-removal-via-rename + recreate;
--  implementation chooses; both meet the contract.)

-- Add new columns
ALTER TABLE pipelines ADD COLUMN is_template BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE pipelines ADD COLUMN cloned_from_pipeline_id UUID NULL REFERENCES pipelines(id);
ALTER TABLE pipelines ADD COLUMN archived_at TIMESTAMPTZ NULL;  -- for archive_pipeline op
ALTER TABLE jobs ADD COLUMN contract JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Add the two new edge types
-- (depending on enum vs check-constraint implementation, this is either an enum value add
--  or a CHECK constraint update. Both meet the contract.)
ALTER TYPE edge_type ADD VALUE 'job_references_decision';
ALTER TYPE edge_type ADD VALUE 'job_references_learning';

-- Migrate seed data: ship-a-thing workflow + 3 steps -> template Pipeline + 3 draft Jobs
-- This is a data migration, not just schema. The seeded contract_profiles values
-- are dropped silently (no consumers).
INSERT INTO pipelines (id, project_id, name, slug, is_template, created_at, ...)
  SELECT gen_random_uuid(), <bootstrap_project_id>, 'ship-a-thing', 'ship-a-thing', true, now(), ...
  FROM workflows WHERE slug='ship-a-thing' AND version=1
  LIMIT 1;
-- Then INSERT INTO jobs SELECT ... FROM workflow_steps WHERE workflow_id=<seeded-workflow-id>
-- with state='draft' and an inline empty contract JSONB.
-- Application-level invariant: the bootstrap project_id is fixed at the founder's first
-- Project, created during `aq setup`. If no Project exists yet, the seed data migration is
-- skipped — the seeded template Pipeline is created lazily on first `aq setup`.
```

The seed migration must be idempotent on a fresh database (cap #12's first-run install path) and on the existing dev/CI database (where the workflow + 3 steps + 4 contract_profile rows already exist).

**Story 3.5.2 — Service-layer + route-layer + CLI + MCP changes**

Remove:
- `apps/api/src/aq_api/services/workflows.py`
- `apps/api/src/aq_api/routes/workflows.py`
- `apps/api/src/aq_api/services/instantiate.py`
- `aq workflow` Typer subcommand group from `apps/cli/src/aq_cli/main.py`
- `aq pipeline instantiate` Typer command
- 5 MCP tools: `create_workflow`, `list_workflows`, `get_workflow`, `update_workflow`, `archive_workflow`
- 1 MCP tool: `instantiate_pipeline`
- Any `app.py` route registrations for the above
- `apps/api/src/aq_api/models/db.py` — drop `Workflow`, `WorkflowStep`, `ContractProfile` SQLAlchemy models
- Pydantic models in cap-03 model modules — drop Workflow / WorkflowStep / ContractProfile contract models
- `apps/api/tests/test_workflows_versioning.py`
- `apps/api/tests/test_workflows_archive.py`
- `apps/api/tests/test_instantiate_pipeline.py`
- `apps/api/tests/test_instantiate_atomicity.py`

Add:
- `apps/api/src/aq_api/services/pipelines.py` — extend with `clone_pipeline` and `archive_pipeline`. Remove the `update_pipeline` 400 rejection of `project_id` if it can be expressed via Pydantic field exclusion (else preserve as-is).
- `apps/api/src/aq_api/routes/pipelines.py` — `POST /pipelines/{id}/clone` route, `POST /pipelines/{id}/archive` route
- `aq pipeline clone --source-id <uuid> --name <str>` CLI command, `aq pipeline archive <id>` CLI command
- `clone_pipeline` and `archive_pipeline` MCP tools with `destructiveHint:false` for clone, `destructiveHint:true` for archive
- `apps/api/tests/test_pipeline_template_and_clone.py` — covers `is_template` filter, clone semantics (3 Jobs in `draft` state with empty contract JSONB, `cloned_from_pipeline_id` set), archive
- New atomicity test: clone with monkeypatched mid-transaction failure → no Pipeline, no Jobs, no audit row

**Story 3.5.3 — OpenAPI + MCP snapshot regeneration + parity test updates**

- Regenerate `tests/parity/openapi.snapshot.json` and `tests/parity/mcp_schema.snapshot.json`.
- Update `tests/parity/test_four_surface_parity.py` — remove all `-k workflow` and `-k profile` and `-k instantiate` parameterized cases. Add `-k clone` and `-k template` cases.
- Update `tests/parity/mcp_harness.py` — remove workflow/profile/instantiate harness invocations, add clone harness.
- Verify `pnpm gen:types` (or whatever the gen:types pipeline runs) produces clean Web TypeScript types from the new OpenAPI snapshot.

**Story 3.5.4 — Evidence pack + cap #3.5 C-checkpoint**

- Run the full Docker test matrix: `docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity`.
- Run `mypy --strict apps/api/src/aq_api/` and `ruff check apps/api apps/cli`.
- Run the seed migration on a fresh DB and on the existing dev DB; verify both succeed.
- Verify on the live DB:
  - `SELECT count(*) FROM workflows` → fails (table dropped). Same for `workflow_steps`, `contract_profiles`.
  - `SELECT column_name FROM information_schema.columns WHERE table_name='jobs' AND column_name IN ('instantiated_from_step_id','contract_profile_id')` → empty.
  - `SELECT column_name FROM information_schema.columns WHERE table_name='pipelines' AND column_name IN ('instantiated_from_workflow_id','instantiated_from_workflow_version')` → empty.
  - `SELECT column_name FROM information_schema.columns WHERE table_name='jobs' AND column_name='contract'` → returns `contract`.
  - `SELECT column_name FROM information_schema.columns WHERE table_name='pipelines' AND column_name IN ('is_template','cloned_from_pipeline_id','archived_at')` → returns all three.
  - `SELECT count(*) FROM pipelines WHERE is_template=true AND name='ship-a-thing'` → returns 1.
- Evidence under `plans/v2-rebuild/artifacts/cap-0305/`.
- Push branch tip. Stop. Post comprehensive evidence on a new AQ2 epic (call it AQ2-CAP-0305-EPIC). Wait for Claude audit. Wait for Ghost approval. **No PR yet** — cap #3.5 squashes into the cap #3 PR at C2.

### Cap #3.5 op-count math

Starting cap-03 op count (per AQ2-39 epic): 28 ops.

After cap #3.5 + AQ2-50 cancellation:
- Lose 5 Workflow ops + 1 instantiate_pipeline = 6 ops gone
- Lose 2 profile ops (AQ2-50 cancelled) = 2 ops gone
- Gain 1 clone_pipeline = 1 op
- Gain 1 archive_pipeline = 1 op (was implicitly missing from the plan; needed to give templates a way to be deactivated)

**Final cap-03 op count after cap #3.5: 22 ops.**

These are: 5 project + 3 labels + 5 pipeline (create, list, get, update, clone) + archive_pipeline + 4 job CRUD + 3 (comment_on_job, list_job_comments, cancel_job) + list_ready_jobs = 22. The cap-3 ops table in `capabilities.md` regenerates from this.

---

## Section 5 — Edits to existing Plane tickets

### AQ2-39 (Capability #3 epic)

**Operation:** Add a new comment on AQ2-39 with the text below. Do not edit the description (the original locked decisions remain a historical record; the comment supersedes them).

> **PLAN UPDATE 2026-04-28 — Cap #3 paused for cap #3.5 schema correction.**
>
> Per `plan-update-2026-04-28.md`, cap #3 work is paused immediately after AQ2-46. A new cap #3.5 epic (AQ2-CAP-0305-EPIC, to be created) inserts a schema consolidation between AQ2-46 and AQ2-47. Cap #3.5 unwinds Workflow tables, Contract Profile tables, the composite FK on jobs, the dead `instantiated_from_*` columns, and folds in AQ2-46's pending mypy/ruff fix-up. After cap #3.5 ships and is approved, cap #3 resumes with modified AQ2-47, AQ2-48, AQ2-49, and rewritten AQ2-51. AQ2-50 is cancelled.
>
> Final cap #3 op count after cap #3.5: 22 ops (was 28).
>
> Locked decisions in this epic that change:
> - F-P0-2 (instantiated_from is direct FK on jobs) — UNWOUND. Provenance moves to `pipelines.cloned_from_pipeline_id`.
> - F-P0-4 (Contract Profile UUID PK + UNIQUE name+version) — UNWOUND. Profiles dropped entirely.
> - F-P0-5 (Workflow slug family identifier) — UNWOUND. No Workflows.
> - F-P0-rev2-1 (workflow_steps.default_contract_profile_id NOT NULL) — UNWOUND. No workflow_steps.
> - F-P1-rev2-7 (composite FK pipeline_id+project_id) — UNWOUND. The denormalization invariant is now enforced at the application layer in clone_pipeline (project_id of clone matches source) instead of at the DB level.
>
> Locked decisions in this epic that survive:
> - F-P0-1 (no draft entry in cap #3) — survives. Cap #3.5 doesn't change job-state semantics. Cap #6 dogfood will exercise `draft` via `clone_pipeline` (which creates Jobs in `draft`, the new exception).
> - F-P1-5 (update_job is metadata-only) — survives. AQ2-47 ticket body remains accurate on this point.
> - F-P1-rev2-5 (list_ready_jobs project_id REQUIRED) — survives.
> - F-P1-rev2-6 (list_job_comments — op count) — survives in shape; the op count itself becomes 22 after cap #3.5.
>
> The existing C1 PASS verdict on AQ2-43 stands. C2 moves to end-of-AQ2-51-as-modified.

### AQ2-47 (Story 3.8 — Job CRUD)

**Operation:** Edit the description. Surgical changes:

In the **Objective** section, change:

> `create_job` (must bind to a Pipeline + Contract Profile by id per F-P0-4)

to:

> `create_job` (must bind to a Pipeline; takes inline `contract JSONB` per the cap #3.5 schema correction — no Contract Profile lookup)

In the **Scope (in)** bullet list, change:

> `create_job` defaults `state='ready'` (per F-P0-1 — no `draft` entry path in cap #3).

to:

> `create_job` defaults `state='ready'` for ad-hoc Jobs (per F-P0-1). Note: `clone_pipeline` (cap #3.5) creates Jobs in `state='draft'` because cloned template Jobs are not yet ready for execution. AQ2-47's `create_job` does not create draft Jobs — the only entry to `draft` is via `clone_pipeline`.

After the existing bullet:

> `update_job` rejects `state` payloads with 400 `cannot_write_state_via_update` and `labels` payloads with 400 `cannot_write_labels_via_update`. Both denials audited.

add a new bullet:

> `update_job` ALSO rejects `contract` payloads with 400 `cannot_write_contract_via_update`. The Contract is set at create time and is immutable. Audited.

In the **Security guardrails** section, change:

> `create_job` inherits Pipeline's `project_id` denormalization (composite FK enforces consistency).

to:

> `create_job` inherits Pipeline's `project_id` denormalization. The composite FK from F-P1-rev2-7 was unwound in cap #3.5; consistency is enforced at the application layer (the service function copies `project_id` from the parent Pipeline at insert time).

Add a new DoD row to the table:

> | DOD-AQ2-S8-05 | `create_job` requires non-null `contract JSONB`; missing contract returns 422 | test | `artifacts/cap-03/create-job-contract-required.xml` | 422 returned for missing/null contract |

Update DoD count from 4 to 5. Update **Submission shape** `dod_results[]` from 4 to 5.

### AQ2-48 (Story 3.9 — Comments + cancel)

**Operation:** No changes required. Story is unaffected by this update.

### AQ2-49 (Story 3.10 — list_ready_jobs)

**Operation:** Edit the description. One surgical change:

In the **Submission shape** section, change:

> `handoff = "AQ2-50 (Story 3.11 Contract Profile discovery)"`

to:

> `handoff = "AQ2-51 (Story 3.12 Parity + CI + atomicity + redact-evidence) — AQ2-50 was cancelled per plan-update-2026-04-28; profile discovery does not exist in v1"`

### AQ2-50 (Story 3.11 — Contract Profile discovery)

**Operation:** Cancel the ticket. Move state to `cancelled`. Add a comment:

> Cancelled per `plan-update-2026-04-28.md` Decision 3. Contract Profiles dropped from v1; the `contract_profiles` table is removed in cap #3.5's schema migration. The seeded profile data is dropped silently (no consumers). MCP description on `create_job` (and on `submit_job` in cap #5) teaches the Contract shape inline; no profile registry is needed for discovery. The ADR-AQ-030 contract structure remains authoritative — it now describes the shape of the inline `contract JSONB` field on each Job rather than entries in a profile registry.

### AQ2-51 (Story 3.12 — Parity + CI + atomicity + redact-evidence — CHECKPOINT C2)

**Operation:** Edit the description. Substantial changes:

In the **Objective**, change:

> Mirror cap #2 Story 2.12. Add cap #3 entities to `tests/parity/`. Update `tests/parity/four_surface_diff.py` to cover new ops. Add atomicity tests for the multi-row mutations (`instantiate_pipeline`, `attach_label`).

to:

> Mirror cap #2 Story 2.12. Add cap #3 entities to `tests/parity/`. Update `tests/parity/four_surface_diff.py` to cover the final cap-03 op set (22 ops, post cap #3.5). Add atomicity tests for the multi-row mutations: `clone_pipeline` (replaces the old instantiate_pipeline atomicity test, which was deleted in cap #3.5) and `attach_label`.

In the **Scope (in)** bullet list, change:

> Parity tests for every cap #3 op (28 ops): `tests/parity/test_four_surface_parity.py` extended.

to:

> Parity tests for every cap #3 op (22 ops, post cap #3.5): `tests/parity/test_four_surface_parity.py` extended. Note: cap #3.5's Story 3.5.3 already removed `-k workflow`, `-k profile`, `-k instantiate` parameterized cases and added `-k clone`, `-k template` cases. AQ2-51 verifies the resulting parity test suite is complete and green for the final 22 ops.

In the same Scope bullet list, change:

> Atomicity test for `instantiate_pipeline`: monkeypatch step-insert mid-transaction → assert no Pipeline, no Jobs, no audit row.

to:

> Atomicity test for `clone_pipeline`: monkeypatch Job-clone mid-transaction → assert no new Pipeline, no new Jobs, no audit row. (The old instantiate_pipeline atomicity test was deleted in cap #3.5.)

Update the DoD table:

- DOD-AQ2-S12-01: change "all 28 cap #3 ops" to "all 22 cap #3 ops".
- DOD-AQ2-S12-02: change "instantiate_pipeline" to "clone_pipeline".
- DOD-AQ2-S12-03: change "cap #3's new tests" to "cap #3's new tests post cap #3.5".
- DOD-AQ2-S12-08: **DELETE this DoD entirely.** It enforces "job_edges contains zero rows with edge_type='instantiated_from' (per F-P0-2)" — F-P0-2 is unwound in cap #3.5. The `instantiated_from` enum value is removed from edge_type.

Add a new DoD:

> | DOD-AQ2-S12-08-NEW | `pipelines.is_template = true` shows exactly one row (the seeded ship-a-thing template); `pipelines.cloned_from_pipeline_id` is queryable | command | `artifacts/cap-03/template-pipeline-shape.txt` | seeded template present after migration; clone test produces a row with non-null cloned_from_pipeline_id |

Update **Submission shape** `dod_results[]` from 8 to 8 (count unchanged after delete-and-add).

### Cap #3.5 epic (NEW — to be created)

**Operation:** Create a new Plane epic. Suggested ID: AQ2-CAP-0305-EPIC (let Plane assign the actual sequence number). Suggested title: "Capability #3.5: Schema consolidation (Workflow→Pipeline collapse + Contract Profile drop + AQ2-46 fix-up)". Description: paste Section 4 of this document, with the Stories list as sub-tickets to be created next.

Sub-tickets to create as children of the cap #3.5 epic:
- Story 3.5.0 — AQ2-46 mypy/ruff fix-up
- Story 3.5.1 — Schema migration (one Alembic revision)
- Story 3.5.2 — Service + route + CLI + MCP changes
- Story 3.5.3 — OpenAPI + MCP snapshot regeneration + parity test updates
- Story 3.5.4 — Evidence pack + cap #3.5 C-checkpoint

---

## Section 6 — Edits to capabilities.md

These are the surgical edits to `capabilities.md` rev 4. Codex applies these directly when Ghost says "publish rev 4."

### Architecture locks → Auth model
No changes.

### Architecture locks → Job lifecycle
No changes to states. The transition op list stays.

### Architecture locks → Two entity types
Replace the entire section with:

> ### One entity type
>
> - **Pipeline** — both reusable templates (`is_template=true`) and execution runs (`is_template=false`). Cloning a template via `clone_pipeline` creates a new Pipeline with `cloned_from_pipeline_id` set; the new Pipeline's Jobs are created in `state='draft'`. Templates are not versioned — change in place; clone before changing if you want to preserve the old shape. Pipelines are soft-deleted via `archive_pipeline`.

### Architecture locks → Job edges (4 types)
Replace with:

> ### Job edges (5 types)
>
> - `gated_on(A, B)` — A blocked from `ready` until B is `done`. Auto-resolves: when B is `done`, AQ re-evaluates Jobs gated on B; A transitions to `ready` only when **all** gates are `done` AND its Contract is complete.
> - `parent_of(A, B)` — hierarchy
> - `sequence_next(A, B)` — ordering only
> - `job_references_decision(J, D)` — manual cross-reference (cap #9). The Decision's primary scope is set by its attachment.
> - `job_references_learning(J, L)` — manual cross-reference (cap #9). Same scoping rule.

### Architecture locks → Contract Profiles
Replace the entire section with:

> ### Contracts (inline)
>
> Every Job carries an inline `contract JSONB NOT NULL` column. The Contract is the Definition of Done — written at `create_job` time, validated only at `submit_job` time per ADR-AQ-030. No registry, no versioning. MCP descriptions on `create_job` and `submit_job` teach the shape.

### Architecture locks → Customization line
Update the table:

| Customizable per Project (governed, versioned) | System invariant (fixed) |
|---|---|
| Custom fields, labels, template Pipelines | Lifecycle states, edge types, submission payload shape, audit log shape, API key model, the four UI views, Contract structure (per ADR-AQ-030) |

(Removed "Contract Profiles" and "Workflows"; added "template Pipelines"; clarified that Contract structure is fixed.)

### Capability list
Insert between cap #3 and cap #4:

> 3.5. `[ ]` Schema consolidation — drop Workflow + Contract Profile tables, fold in AQ2-46 fix-up, regenerate parity surface for the 22-op cap-03 surface

### Cap #3 description
The cap #3 capability stays in the document as a historical record of what was originally planned and what shipped (AQ2-40 through AQ2-46 + AQ2-47 through AQ2-49 + AQ2-51, modified per Section 5). Add a paragraph at the end of the cap #3 description:

> **Note (2026-04-28):** Stories 3.5 (AQ2-44) and 3.7 (AQ2-46) shipped Workflow versioning and instantiate_pipeline as originally spec'd, then were unwound by cap #3.5. The corresponding tables, ops, and FK constraints no longer exist after cap #3.5 ships. Story 3.11 (AQ2-50, Contract Profile discovery) was cancelled. Final cap #3 op count after cap #3.5: 22 ops.

### New cap #3.5 description
Add the full cap #3.5 capability description from Section 4 of this document.

### Cap #5 description
Update the description per Decision 4 (Contract requires `decisions_made[]` and `learnings[]`; submit creates D&L nodes inline). Remove `register_contract_profile` and `version_contract_profile` from the op list. Remove the "MCP `Resources` start here" subsection (no Contract Profile resources to expose).

### Cap #7 description
Replace per Decision 1. Title becomes "Run queries (read-only views over audit log)." `list_runs` and `get_run` query the audit log via partial index. No new tables.

### Cap #8 description
Update per Decision 4 — Context Packet returns structural pointers to attached Decisions and Learnings on the Job, its Pipeline, its Project. No relevance ranking. No content.

### Cap #9 description
Update per Decision 4 — capture is at submit time AND standalone; attachment IS the scope; no separate scope field. Update op signatures.

### Cap #10 description
Update edge type count from 4 to 5. Drop `instantiated_from`. Add the two new generic types.

### Cap #11 description
Reinforce: read-only forever per Decision 6.

### Op coverage table
Regenerate. Final v1 op count after all decisions: **48 ops** (was 56). Detailed table to be produced as part of rev 4.

### Backlog section
Add the new rows from Decisions 5, 6, and the cap #3.5 cleanup itself (e.g., "Re-introduce strict project_id consistency at DB level" if you ever want the composite FK back). Update existing rows per Decisions 3, 5, 6.

---

## Section 7 — AQ2-46 mypy/ruff fix-up status

Per Claude's audit comment on AQ2-46 (2026-04-27), three issues were flagged:

```
apps/api/src/aq_api/services/instantiate.py:23: error: Argument "state" to "Job" has
  incompatible type "str"; expected "Literal[...]"
apps/api/src/aq_api/services/instantiate.py:42: error: Returning Any from function
  declared to return "Workflow | None"
apps/api/tests/test_instantiate_pipeline.py:422:89: E501 Line too long (92 > 88)
```

These were not addressed in a separate commit before cap #3 paused. **Cap #3.5 absorbs them in Story 3.5.0.** Note that `services/instantiate.py` and `test_instantiate_pipeline.py` are both deleted in Story 3.5.2 — Story 3.5.0 fixes them anyway, so the cap-03 branch passes mypy strict + ruff at every commit boundary, before the deletion lands. This preserves the per-story gate discipline (one green commit per story) that cap #1 and cap #2 established.

---

## Section 8 — What does not change

Explicitly unchanged for agent clarity:

- **Cap #1** (Four-surface ping) — done, validated, merged at `96e158d`.
- **Cap #2** (Actor identity, Bearer auth, audit log) — unchanged. The audit log gains importance because it now backs cap #7's queries, but no schema changes.
- **Cap #3 stories AQ2-40, AQ2-41, AQ2-42, AQ2-43** — done, approved, no rework.
- **Cap #3 stories AQ2-44, AQ2-45, AQ2-46** — done, approved (AQ2-46 with pending fix-up). Their code and migrations stay in place until cap #3.5 unwinds them.
- **Cap #4** (Atomic claim) — unchanged. Heartbeat lease, label_filter, MCP richness pattern all stay. Cap #4 builds against the post-cap-#3.5 schema.
- **Cap #6** (Mario dogfoods one ticket) — unchanged in structure. Composes ops from #1–#5 (with the modified op surface). The dogfood now exercises `clone_pipeline` and `create_job(contract=...)` instead of `instantiate_pipeline` and `create_job(contract_profile_id=...)`.
- **Cap #10** (Edges + gated_on resolver) — mostly unchanged. The edge type enum drops `instantiated_from` (already removed in cap #3.5) and gains `job_references_decision` + `job_references_learning`. The resolver logic is unchanged.
- **Cap #12** (Install + first-run migration) — unchanged. Will need to incorporate cap #3.5's migration in the migration chain (`0001` cap-1 → `0002` cap-2 → `0003` cap-2 → `0004` cap-3 → `0005` cap-3.5), but no functional change.
- **The Pact** (Architecture locks) — unchanged in spirit. The "two entity types" line becomes "one entity type." The "4 edge types" line becomes "5 edge types." The "Contract Profiles" section becomes "Contracts (inline)." All other Pact rules unchanged.
- **ADR-AQ-019** (lexicon) — unchanged.
- **ADR-AQ-021** (MCP transports) — unchanged.
- **ADR-AQ-030** (Contract structure) — unchanged in *shape*. The Contract structure described by ADR-AQ-030 is now the JSON schema for the inline `contract JSONB` on Jobs, instead of the schema for entries in a Contract Profile registry.

---

## Section 9 — Action items

### Codex (implementer)

1. **Stop on `aq2-cap-03`.** Do not claim AQ2-47.
2. Wait for Ghost to create the cap #3.5 epic (AQ2-CAP-0305-EPIC) and its 5 stories.
3. Claim Story 3.5.0 (AQ2-46 mypy/ruff fix-up). Read this document's Section 7 in full. Read the referenced source files. Apply the three fixes. Run the verification commands. One commit. Push.
4. After Claude approves Story 3.5.0, claim Story 3.5.1 (Schema migration). Read Section 4 in full. Write the Alembic revision `0005_cap0305_schema_consolidation`. Run on a fresh DB and the existing dev DB. Verify both succeed. One commit. Push.
5. After Claude approves Story 3.5.1, claim Story 3.5.2 (Service + route + CLI + MCP changes). This is the largest story by file count — read Section 4's Story 3.5.2 list completely before starting. Removals first, then additions. One commit. Push.
6. After Claude approves Story 3.5.2, claim Story 3.5.3 (Snapshot regeneration + parity updates). Regenerate snapshots, update parity tests. One commit. Push.
7. After Claude approves Story 3.5.3, claim Story 3.5.4 (Evidence pack + cap #3.5 C-checkpoint). Run the full validation. Post comprehensive evidence on the cap #3.5 epic. **STOP.** Wait for Ghost approval before starting cap #3 resumption.
8. After Ghost approves cap #3.5, return to cap #3 work. Read AQ2-47's edited description (per Section 5). Claim AQ2-47. Implement against the post-cap-#3.5 schema (inline `contract` JSONB; no contract_profile_id; cap #3.5 migration in the chain). Continue cap #3 through AQ2-51 normally.
9. AQ2-51 produces evidence for the modified C2 checkpoint (22 ops, not 28). When AQ2-51 is approved by Claude, open ONE PR for all of cap #3 + cap #3.5. Wait for Ghost merge approval. Do NOT self-merge.

### Claude (planner/auditor)

1. Read this document completely.
2. Audit Codex's Story 3.5.0 against the three specific mypy/ruff/E501 issues called out in Section 7.
3. Audit Codex's Story 3.5.1 schema migration: verify all listed tables/columns are dropped, all listed columns are added, the seed data migration produces exactly one `is_template=true` Pipeline named `ship-a-thing` with three Jobs in `draft` and empty contract JSONB.
4. Audit Codex's Story 3.5.2: verify all listed source files, test files, models, CLI commands, MCP tools, and routes are removed. Verify all listed additions exist. Verify the new clone atomicity test passes failure injection.
5. Audit Codex's Story 3.5.3: verify the regenerated snapshots reflect the 22-op cap-03 surface (not 28). Verify the parity tests cover the new op surface and don't reference the removed ops.
6. Audit Codex's Story 3.5.4: verify the full Docker test matrix passes. Verify the DB query checks all return as expected.
7. After cap #3.5 ships, return to cap #3 audits with the modified ticket bodies as the spec.
8. From cap #4 onward, treat Section 8 of this document as the boundary. Any submission that references Workflows, Contract Profiles, the Run Ledger table as a separate entity, or webhook subscriptions for agent wake-up is a regression — flag it.

### Ghost (oversight)

1. Review this document. Flag anything misremembered or mis-decided. Once committed, the agents treat it as authoritative.
2. **Decision needed:** when to publish `capabilities.md` rev 4. Recommendation: immediately after committing this update, so agents work against the final spec for cap #3.5 and beyond.
3. **Decision needed:** AQ → Orxa rename. Holding per current instruction. When ready, the rename is a single find-and-replace pass on `AQ 2.0 → Orxa` and `AgenticQueue → Orxa` across `capabilities.md`, this document, the README, all Plane epic/story bodies, and source code identifiers (package name, CLI binary, env var prefix, MCP server name). The rename is mechanical but touches a lot of files; it should be its own commit.
4. **Action needed in Plane:**
   - Create the cap #3.5 epic (suggested title: "Capability #3.5: Schema consolidation").
   - Create the 5 sub-stories under that epic per Section 4.
   - Cancel AQ2-50 with the comment from Section 5.
   - Edit AQ2-47, AQ2-49, AQ2-51 ticket descriptions per Section 5.
   - Comment on AQ2-39 epic per Section 5.
5. **Action needed in repo:** commit this file at `D:\mmmmm\mmmmm-aq2.0\plans\v2-rebuild\plan-update-2026-04-28.md`. Do not commit `capabilities.md` rev 4 yet — that lands separately when you're ready.

---

## Provenance

- This update reflects decisions made in the 2026-04-28 strategy conversation between Ghost and Claude.
- Live state of cap #3 was validated against Plane via MCP on 2026-04-28: AQ2-39 epic, AQ2-40 through AQ2-51 stories all read in full.
- Authority: Ghost confirmed all six decisions and Path Y1-Insert (cap #3.5 between AQ2-46 and AQ2-47) at the close of the conversation.
- Companion artifact: `capabilities.md` rev 4 (to be produced) will fold these deltas into the canonical spec. Until that is published, this document is authoritative on conflict.
- Filed under: `D:\mmmmm\mmmmm-aq2.0\plans\v2-rebuild\plan-update-2026-04-28.md`.
