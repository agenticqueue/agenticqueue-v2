# Plan: AQ 2.0 Capability #3 — Project, Workflow, Pipeline, Job entities with full CRUD; one seeded static Workflow template

## Context

Cap #1 (Four-surface ping) is on `main` at `96e158d`. Cap #2 (Authenticated Actors + Bearer auth + same-transaction audit log) is on `main` at `dc4ad37`. Cap #3 is the entity foundation that every later capability builds on — until cap #3 ships, no Job can exist, no claim can happen (cap #4), and no submission can be validated (cap #5).

This plan is **rev 3**, written after rev 1 (12 findings from Gemini + Codex) and rev 2 (Codex re-audit: 1 new P0 + 7 P1) were folded in on 2026-04-27. Gemini's rev 2 audit was PASS (100% — "plan is locked & ready"). Twenty findings total resolved at the plan level before any coding starts. Every locked decision below is intentional and grep-verifiable; every story carries a "Why this matters (human outcome)" line; every DoD has a real verification command and an artifact path.

Cap #3 ships **6 entities** (Project, Label, Workflow, Pipeline, Job, Contract Profile) and **28 ops** spanning REST + CLI + MCP. (Note: capabilities.md original ops list shows 26 — AQ2-36 added `list_ready_jobs` (→27), and rev 3 adds `list_job_comments` per F-P1-rev2-6 (→28).) The web tier gets no new views (Pact: cap #11 owns UI work). Three Linear/Jira analogues are explicitly forbidden after side-by-side gap analysis: **Cycle/Sprint** (agents need a queue, not a calendar), **Initiative/Program** (Projects are the top-level container), and **Project Status Update** (audit log + Job comments are sufficient). These forbids are repeated in the Out-of-scope section so future-Mario doesn't re-litigate.

### Findings folded in from rev 1 audit (Gemini + Codex, 2026-04-27)

**P0 — schema / contract bugs that would have blocked execution:**
- **F-P0-1 (G-1, C-1) — draft→ready dead end.** Original Story 3.7 created instantiated Jobs in `draft` AND Locked Decision 8 forbade `update_job` from writing state. Result: instantiated Jobs were unreachable. **Fix:** `instantiate_pipeline` creates Jobs in `ready` directly. `draft` is reserved for cap #10's `gated_on` mechanism and never entered in cap #3.
- **F-P0-2 (G-2, C-2) — `job_edges.to_job_id` polymorphism.** Original Locked Decision 2 had `job_edges.to_job_id` FK to `jobs`, but `instantiated_from` should point to a workflow_step. **Fix:** drop `instantiated_from` from `job_edges.edge_type`. Add `jobs.instantiated_from_step_id UUID NULL REFERENCES workflow_steps(id) ON DELETE RESTRICT`. `job_edges` is strictly Job-to-Job for `gated_on`, `parent_of`, `sequence_next`.
- **F-P0-3 (C-3) — seeded rows can't have `created_by_actor_id`.** Migration runs before setup mints the founder. **Fix:** `created_by_actor_id` is NULLABLE on every cap #3 entity. Seeded rows are NULL = "system-owned." App routes enforce non-NULL on authenticated-user creates.
- **F-P0-4 (C-4) — Contract Profile versioning blocked.** `contract_profiles.name PK` + `jobs.contract_profile_name FK` would break when cap #5 versions profiles. **Fix:** `contract_profiles.id UUID PK, name TEXT, version INT, UNIQUE (name, version)`. `jobs.contract_profile_id UUID FK`.
- **F-P0-5 (C-5) — Workflow version/archive identity.** Row-per-version + "instantiate uses latest non-archived" + "archive only the latest" produced ambiguous semantics. **Fix:** `workflows.slug TEXT NOT NULL` is the family identifier. `UNIQUE (slug, version)`. `instantiate_pipeline` accepts slug; resolves to highest non-archived version. `archive_workflow` accepts slug and sets `is_archived=TRUE` on ALL rows in the family.

**P1 — cleanups:**
- **F-P1-1 (G-3) — Workflow step duplication on update.** Story 3.5 was silent on whether `update_workflow` carries forward the steps. **Fix:** explicit — `update_workflow` inserts a new `workflows` row (v+1) AND a new set of `workflow_steps` from the request payload tied to the new workflow_id. Old steps untouched.
- **F-P1-2 (C-P1-3) — Workflows / Contract Profiles project-scoped per Pact, global in plan.** **Fix:** declared deviation — v1 ships them global; project-scoping deferred to cap #5 or later.
- **F-P1-3 (C-P1-4) — claim index missing `project_id`.** Original index was `(state, pipeline_id, created_at)`; cap #4's claim path needs `project_id`. **Fix:** denormalize `jobs.project_id` (FK→projects, NOT NULL, immutable after create). Index becomes `(state, project_id, created_at)` partial WHERE state='ready'.
- **F-P1-4 (C-P1-5) — "content-addressable" was misleading.** No content_hash exists. **Fix:** rename to "versioned" everywhere.
- **F-P1-5 (C-P1-6) — `update_job` "label attachments" overlapped `attach_label`/`detach_label`.** Two mutation paths = consistency risk. **Fix:** `update_job` accepts only `title` and `description`. Labels mutated exclusively via `attach_label`/`detach_label`.
- **F-P1-6 (C-P1-1) — op count 26 vs 27.** capabilities.md text shows 26; AQ2-36 added `list_ready_jobs` making it 27. **Fix:** noted in Context above.
- **F-P1-7 (C-P1-2) — validation walk dead-ended on draft.** Resolved automatically by F-P0-1 fix.

**Ghost's concern (state-machine completeness):** Confirmed complete for v1. Locked states `draft → ready → in_progress → done | failed | blocked | pending_review | cancelled`. After F-P0-1, cap #3 never enters `draft` at all (reserved for cap #10's `gated_on` resolver). Edit capabilities are covered by per-entity update ops (`update_project`, `update_workflow` versioning, `update_pipeline`, `update_job` metadata, `attach_label`/`detach_label`, `cancel_job`). One unrelated gap noticed: capabilities.md line 27 lists `review_complete` as a transition with no capability owner — out of cap #3 scope, file separately.

### Findings folded in from rev 2 audit (Codex re-audit, 2026-04-27)

**P0 — new execution blocker:**
- **F-P0-rev2-1 — `instantiate_pipeline` underspecified inputs.** Story 3.7 created Pipelines + Jobs but the request never specified `project_id`, `pipeline_name`, OR a `contract_profile_id` mapping for the Jobs. Both `pipelines.project_id` and `jobs.contract_profile_id` are NOT NULL. **Fix:** add `workflow_steps.default_contract_profile_id UUID NOT NULL` REFERENCES `contract_profiles(id)` so each Workflow step carries its own default profile. The `instantiate_pipeline` request body becomes `{project_id, pipeline_name}`. Profiles flow from the Workflow step definitions; per-step override at instantiate time deferred to cap #5+. The seeded `ship-a-thing` Workflow's three steps get realistic defaults: `scope` → `research-decision`, `build` → `coding-task`, `verify` → `bug-fix`.

**P1 — cleanups + DB integrity hardening:**
- **F-P1-rev2-1 — Locked Decision 17 contradicted rev 2 edge model.** Rev 2 hoisted `instantiated_from` off `job_edges` onto `jobs.instantiated_from_step_id`, but Decision 17 still said "the only edges that get inserted in cap #3 are `instantiated_from` edges." **Fix:** rewrite Decision 17 to "Cap #3 exposes zero edge-mutating ops and writes zero `job_edges` rows."
- **F-P1-rev2-2 — Out-of-scope section still said instantiated Jobs are `draft`.** Direct contradiction with F-P0-1. **Fix:** rewrite the bullet to "all cap #3-created Jobs start `ready`."
- **F-P1-rev2-3 — MCP output content bundling text said "draft."** Same contradiction. **Fix:** rewrite to "ready" / "immediately claimable" matching the server-instructions bullet.
- **F-P1-rev2-4 — `update_workflow` against stale version was undefined.** If v2 exists and someone tries `update_workflow(v1.id)`, the new row's `(slug, version+1)` collides with v2. **Fix:** lock — reject non-latest workflow ids with `409 workflow_not_latest`. Caller must resolve to family latest themselves before calling update.
- **F-P1-rev2-5 — `list_ready_jobs(project)` semantics underspecified.** **Fix:** `project_id` is REQUIRED, matching cap #4's claim symmetry. The GIN-and-btree index plan only makes sense with project filtering pinned.
- **F-P1-rev2-6 — `get_job` had no comments readability path.** `comment_on_job` writes to `job_comments`; nothing reads from it. **Fix:** add `list_job_comments(job_id, limit, cursor)` as a read op. Op count goes from 27 to 28. Story 3.9 grows to include the list op.
- **F-P1-rev2-7 — `jobs.project_id` not DB-enforced consistent with `pipelines.project_id`.** Independent FKs allow inconsistent rows. **Fix:** add `UNIQUE (id, project_id)` on `pipelines` (allows it as a composite-FK target since `id` is already PK), then add composite FK `(pipeline_id, project_id) → pipelines(id, project_id)` on `jobs`. Postgres enforces consistency mechanically; no app-level guard needed.

### Why cap #3 matters (human outcome)

After cap #3 ships, AQ 2.0 has the entity bedrock to **describe real work**, not just "who did the thing." The human can:
- Create a `Project` and tag it with `Label`s.
- Define a `Workflow` template (`ship-a-thing` ships seeded with three steps) and `instantiate` a `Pipeline` from it — the Pipeline freezes the Workflow's version so editing the template never breaks running work.
- Create `Job`s in that Pipeline, bind each to a `Contract Profile` (`coding-task`, `bug-fix`, `docs-task`, `research-decision`), comment on them, cancel them.
- Read `list_ready_jobs(label_filter=["area:web"])` to preview the queue before claim — the same filter semantics cap #4's `claim_next_job` will use. An MCP-connected agent can scope itself to the work it's good at without AQ needing an agent-capability registry.
- Watch the audit log capture every Project/Workflow/Pipeline/Job mutation with the same same-transaction guarantee cap #2 locked.

What cap #3 deliberately does **not** ship: claim atomicity (cap #4), submit validation (cap #5), edge auto-resolution (cap #10), UI views (cap #11), Cycles/Sprints (forbidden), Initiatives (forbidden), Project Status Updates (forbidden).

---

## Hard preconditions (must be on `main` before cap #3 first commit)

| Ticket | Title | Status |
|---|---|---|
| AQ2-21 | Capability #2 epic | **MERGED** ✓ (`dc4ad37`) |
| AQ2-36 | capabilities.md cap #3 + #4 amendments | **MERGED** ✓ (`6841155`) |
| AQ2-13 | CodeQL workflow + weekly cron evidence | **DONE** ✓ (cron fired 2026-04-27 12:07 UTC) |

Open follow-ups that **do not block** cap #3 start:
- AQ2-34 — expand Dependabot npm wildcard major-ignore (independent; ship in parallel).
- AQ2-38 — triage 6 open Dependabot alerts (3 FastMCP CVEs + 3 transitive). `claude` C2 audit confirmed cap-02 doesn't activate the vulnerable code paths; bumps will land in AQ2-38's branch and merge to main before cap #3 needs the patched fastmcp.

---

## Capability statement (verbatim from `plans/v2-rebuild/capabilities.md`)

> All four core domain entity types exist with full CRUD ops on every surface; Contract Profiles can be discovered (list + describe); one static Workflow template (`ship-a-thing`) ships seeded so dogfooding can begin in capability #6.

**Depends on:** Cap #2 (auth gates every mutation).

---

## Locked decisions for cap #3

These are cap #3-specific commitments **beyond** what cap #1 + cap #2 already locked. Every story below honors all of them.

1. **One Alembic migration revision** (`0004_cap03_entities`) creates **all** cap #3 tables in a single transaction. No incremental "add table per story" — cap #3 is fat by design (capabilities.md: "deliberately fat — entity schemas are the bedrock and they all need to exist together for the graph to be coherent").

2. **Tables introduced (lock the names + nullability):**
   - `projects` — id (UUID PK), name TEXT, slug TEXT UNIQUE, description TEXT, archived_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL, **`created_by_actor_id UUID NULL`** REFERENCES actors(id) (NULL for any seeded rows; non-NULL enforced by app routes for user creates per F-P0-3).
   - `labels` — id (UUID PK), project_id (FK→projects), name TEXT, color TEXT, created_at TIMESTAMPTZ NOT NULL, archived_at TIMESTAMPTZ NULL. UNIQUE (project_id, name) WHERE archived_at IS NULL. **Registry-only**: which label names exist per project.
   - `workflows` — id (UUID PK), **`slug TEXT NOT NULL`** (family identifier), name TEXT, version INT NOT NULL, is_archived BOOL NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL, **`created_by_actor_id UUID NULL`** REFERENCES actors(id), supersedes_workflow_id UUID NULL (self-FK to previous version). **`UNIQUE (slug, version)`**. Family-level archival semantics per F-P0-5: `archive_workflow(slug)` sets `is_archived=TRUE` on **all** rows in the family.
   - `workflow_steps` — id (UUID PK), workflow_id (FK→workflows), name TEXT, ordinal INT, **`default_contract_profile_id UUID NOT NULL`** REFERENCES contract_profiles(id) (per F-P0-rev2-1 — each step carries its own default profile, used by `instantiate_pipeline` to bind Jobs), step_edges JSONB (sketch shape; cap #10 owns step-edge semantics).
   - `pipelines` — id (UUID PK), project_id (FK→projects), name TEXT, instantiated_from_workflow_id UUID NULL (FK→workflows.id), instantiated_from_workflow_version INT NULL, created_at TIMESTAMPTZ NOT NULL, **`created_by_actor_id UUID NULL`**. **`UNIQUE (id, project_id)`** (per F-P1-rev2-7 — required as composite-FK target so `jobs (pipeline_id, project_id)` can reference and Postgres enforces consistency).
   - `jobs` — id (UUID PK), pipeline_id (FK→pipelines), **`project_id UUID NOT NULL`** REFERENCES projects(id) — denormalized from pipeline_id at create time, immutable after (per F-P1-3); **`FOREIGN KEY (pipeline_id, project_id) REFERENCES pipelines(id, project_id)`** (per F-P1-rev2-7 — composite FK enforces denormalized consistency at DB level); state TEXT NOT NULL CHECK IN ('draft','ready','in_progress','done','failed','blocked','pending_review','cancelled'); title TEXT, description TEXT, **`contract_profile_id UUID NOT NULL`** REFERENCES contract_profiles(id) (per F-P0-4 — FK by id, not name); **`instantiated_from_step_id UUID NULL`** REFERENCES workflow_steps(id) ON DELETE RESTRICT (per F-P0-2 — set by `instantiate_pipeline`); labels TEXT[] NOT NULL DEFAULT '{}'; claimed_by_actor_id UUID NULL; claimed_at TIMESTAMPTZ NULL; claim_heartbeat_at TIMESTAMPTZ NULL (column exists but no UPDATE path until cap #4); created_at TIMESTAMPTZ NOT NULL; **`created_by_actor_id UUID NULL`**.
   - `job_edges` — from_job_id UUID (FK→jobs), to_job_id UUID (FK→jobs), edge_type TEXT CHECK IN ('gated_on','parent_of','sequence_next'). **NO `instantiated_from` edge type** (per F-P0-2 — that lives on `jobs.instantiated_from_step_id`). UNIQUE (from_job_id, to_job_id, edge_type). Strictly Job-to-Job.
   - `job_comments` — id (UUID PK), job_id (FK→jobs), author_actor_id (FK→actors), body TEXT, created_at TIMESTAMPTZ NOT NULL.
   - `contract_profiles` — **`id UUID PK`**, name TEXT NOT NULL, version INT NOT NULL, description TEXT, required_dod_ids JSONB, schema JSONB. **`UNIQUE (name, version)`**. Seeded with the four v1 profiles (each as v1) in the migration. Per F-P0-4 — Jobs FK by `id`, so cap #5 can mint v2 of any profile without breaking old Jobs.

3. **GIN index** `idx_jobs_labels_gin` on `jobs.labels` USING gin. **Locked**: every `list_ready_jobs(label_filter)` and (in cap #4) `claim_next_job(label_filter)` query MUST hit this index, not the registry-junction. Verified per Story 3.10 with `EXPLAIN (ANALYZE, BUFFERS)` artifact committed.

4. **B-tree index** `idx_jobs_state_project_created` on `jobs (state, project_id, created_at)` partial WHERE state = 'ready'. Cap #4's claim path needs this; we ship it now so cap #4 doesn't re-migrate. Per F-P1-3 — uses `project_id` (denormalized) not `pipeline_id`, so the claim query doesn't JOIN to `pipelines`.

5. **`labels` denormalized cache** (per AQ2-36 amendment): `attach_label`/`detach_label` mutations write to BOTH the registry-junction (if a junction is implemented) AND the `jobs.labels` TEXT[] cache atomically inside one transaction. Implementation may collapse the registry to TEXT[]-only if name validation is enforced at write-time — that's a Story 3.4 decision, captured in the migration commit body.

6. **Workflow versioning (per F-P0-5 + F-P1-1 + F-P1-4).** `slug` is the family identifier. `update_workflow` does NOT mutate the existing row; in one DB transaction it (a) inserts a new `workflows` row with same `slug`, `version = old.version + 1`, `supersedes_workflow_id = old.id`, AND (b) inserts a new set of `workflow_steps` from the request payload, tied to the new workflow_id. The OLD workflow row and its workflow_steps remain untouched and continue to be readable by Pipelines that snapshotted that version. `archive_workflow(slug)` sets `is_archived = TRUE` on **all** rows in the family — "retire this Workflow entirely" semantics. (Term used in rev 1 — "content-addressable" — was misleading; rev 2 uses "versioned" only. There is no content_hash; identity is `(slug, version)`.)

7. **`instantiate_pipeline` (per F-P0-1 + F-P0-2)** is the most complex op in cap #3 and gets its own story (3.7). It accepts a Workflow `slug`, resolves to the highest non-archived version, creates the Pipeline with `instantiated_from_workflow_id` and `instantiated_from_workflow_version` set, and inserts one Job per Workflow step **in `state = 'ready'`** (not `draft`) with `jobs.instantiated_from_step_id` set to the source step's id (NOT a `job_edges` row — that's strictly Job-to-Job). All inside one DB transaction with one audit row capturing the snapshot fingerprint (workflow_id + version + slug + step count + new job_ids).

8. **`update_job` is metadata-only (per F-P1-5).** It accepts ONLY `title` and `description`. It REJECTS any payload field that maps to `state`, `claimed_by_actor_id`, `claimed_at`, `claim_heartbeat_at`, or `labels` (labels are mutated exclusively via `attach_label`/`detach_label` per F-P1-5 — single canonical path). Rejection returns HTTP 400 `error_code='cannot_write_state_via_update'` (or `'cannot_write_labels_via_update'` for labels) and an audit row recording the rejected field name. State transitions only via `claim_next_job` (cap #4), `submit_job` (cap #5), `cancel_job` (cap #3), `reset_claim` (cap #4), `release_job` (cap #4). After F-P0-1 — cap #3 never enters `draft` state at all; `draft` is reserved for cap #10's `gated_on` mechanism.

9. **`cancel_job` is the only state-mutating op cap #3 ships.** It transitions any non-terminal Job to `cancelled` and writes an audit row. Permitted from any state in `{draft, ready, in_progress, blocked, pending_review}`. From `done` / `failed` / `cancelled` it returns 409 `error_code='already_terminal'` with audit row.

10. **`list_ready_jobs` is read-only and never audited** (matches cap #2 reads-not-audited lock). Returns jobs in `state='ready'` ordered FIFO by `created_at, id`, filtered by `labels @> :label_filter` (using the GIN index), paginated with opaque cursor, `limit <= 100`. Same filter semantics cap #4's `claim_next_job` will use — story 3.10 commits that contract.

11. **`comment_on_job` is a mutation and IS audited** (per cap #2 mutation-always-audited rule). The comment body itself goes into `job_comments`; the audit row captures `op='comment_on_job', target_kind='job', target_id=job_id, request_payload={"body_length": N}` (NOT the body text — keeps audit_log small, body lives in job_comments).

12. **Contract Profiles seeded by the migration**, not via runtime API. Cap #3 reads them; cap #5 will add `create_contract_profile` and `update_contract_profile` for runtime authoring. The four v1 profiles (`coding-task`, `bug-fix`, `docs-task`, `research-decision`) ship with placeholder DoD ids and schemas; full profile content is in the migration body and pinned by tests.

13. **One static Workflow seeded by the migration:** `ship-a-thing` v1 with three steps. Names: `scope`, `build`, `verify`. Three steps create three Jobs when `instantiate_pipeline` runs against this Workflow.

14. **No agent-capability registry** (carry-forward lock from AQ2-36 amendment to cap #4). Agents do not declare profiles to AQ. Routing is caller-side via `label_filter` only.

15. **No `parallel_safe` file-conflict flag** (carry-forward lock from AQ2-36 amendment to cap #4). Two `ready` Jobs without a `gated_on` edge between them are eligible to be claimed concurrently even if they touch the same file. Application-level conflict is the agents' problem, not AQ's.

16. **Labels are project-scoped.** A label `area:web` registered in Project A is a separate row from a label `area:web` registered in Project B. The `jobs.labels` TEXT[] cache stores the simple name (e.g., `area:web`), but every label attachment is validated against `labels WHERE project_id = jobs.pipeline.project_id AND name = :label_name AND archived_at IS NULL`. Cross-project label leakage returns 403 `error_code='label_not_in_project'` with audit row.

17. **Job edges (the `job_edges` table) are insert-only via specific ops in later caps (per F-P1-rev2-1).** Cap #3 creates the table but **exposes ZERO edge-mutating ops AND writes ZERO `job_edges` rows**. The Job-to-Workflow-step relationship lives on `jobs.instantiated_from_step_id`, not in `job_edges`. Cap #5 will add `add_gated_on_edge` as the first writer. Cap #10 will add edge auto-resolution.

18. **`update_workflow` rejects stale-version updates (per F-P1-rev2-4).** If the caller passes a workflow id whose row is NOT the highest-version non-archived row in its slug family, the op returns `409 workflow_not_latest` with audit row. Caller must resolve to family latest before calling update. This prevents `(slug, version+1)` collisions when concurrent updates race against the same family.

19. **`list_ready_jobs(project_id)` requires project_id (per F-P1-rev2-5).** Not optional. Symmetric with cap #4's `claim_next_job`. Removes ambiguity about whether the GIN-and-btree index plan covers global vs project-scoped queues — answer is project-scoped only, and `claim_next_job` will inherit this.

20. **`list_job_comments` is the read path for comments (per F-P1-rev2-6).** Cap #3 ships op count 28 (was 27 in rev 2). Story 3.9 grows to include `list_job_comments(job_id, limit, cursor)` — read-only, never audited, paginated FIFO ordered by `created_at, id`. `get_job` does NOT return comments inline (could be large); the `list_job_comments` op is the canonical reader.

---

## Carry-forward locked rules from cap #1 + cap #2

Every cap #3 story honors all of these. Repeated for grep-recall, not re-litigation.

**From cap #1:**
- Z-form datetime via `aq_api._datetime.parse_utc`. All timestamps timezone-aware UTC.
- Single Pydantic source of truth — no surface re-declares contract.
- Real-stack validation: `docker compose down && up -d --build --wait` + `_assert_commit_matches_head()`.
- Strict ADR-AQ-030 evidence — every artifact under `plans/v2-rebuild/artifacts/cap-03/`, redacted via `scripts/redact-evidence.sh` before commit.
- Four-surface parity discipline: REST + CLI + MCP + Web (Web no-op for cap #3 since no new views).

**From cap #2:**
- All API + MCP handlers `async def`. Never sync.
- Postgres 16-alpine, internal-only network, `aq2_pg_data` named volume.
- SQLAlchemy 2.x async (asyncpg) at runtime; psycopg sync for Alembic only.
- Argon2id `time_cost=2, memory_cost=65536, parallelism=2` for any new key-bearing path (cap #3 introduces no new keys).
- Application-side hashing; `pgcrypto` only for `gen_random_uuid()`.
- iron-session for Web; `AQ_COOKIE_SECURE` env-driven (no NODE_ENV gating).
- In-process service layer: REST + MCP handlers call same Python service functions.
- MCP HTTP requires caller's Bearer; no bridge actor; `agent_identity` decorative-only.
- Setup is auditless (cap #3 doesn't touch setup).
- Reads NEVER audited; mutations ALWAYS audited including denials with `error_code` set.
- Same-transaction audit guarantee: `audited_op` context manager from cap #2.
- Three-layer secret redaction: regex-recursive in app + `scripts/redact-evidence.sh` for artifacts + gitleaks workflow.
- HMAC-SHA256 lookup_id is the auth lookup primitive; SHA256 inside HMAC is NOT a password hash (CodeQL false-positive dismissal recorded on AQ2-21).
- HMAC server secret rotation invalidates all keys; no dual-secret rotation.
- All evidence committed under `plans/v2-rebuild/artifacts/cap-NN/`, redacted before commit.

---

## Out of scope (explicit forbids)

Repeated from capabilities.md cap #3 scope guardrails plus the three Linear/Jira gaps Mario forbade after side-by-side gap analysis on 2026-04-27.

**From capabilities.md:**
- No `claim_next_job` — Jobs can be created/edited but not claimed. Atomic claim ships in cap #4.
- No `submit_job` — submission validation ships in cap #5.
- No `instantiated_from` edge semantics in app code — the column exists, the data is written by `instantiate_pipeline`, but cap #10 owns the auto-resolution logic that reads it.
- No Contract Profile creation/versioning — only read (list + describe). Profile authoring lands in cap #5.
- No UI views — REST/CLI/MCP only. Web tier touches zero pages in cap #3.
- No automatic state transitions — `gated_on` auto-resolution lands in cap #10. **All cap #3-created Jobs start `ready`** (per F-P0-1 + F-P1-rev2-2 — both `create_job` AND `instantiate_pipeline` create Jobs in `ready`). Cap #3 never enters the `draft` state.

**Forbidden after gap analysis (Mario decision 2026-04-27):**
- **No Cycle / Sprint entity.** AQ users are agents, not humans on a calendar. Work sits in `ready` until claimed. No time-boxing in v1, no v1.1, no roadmap. If a future capability needs scheduled work, file a new ticket; do NOT reopen this lock.
- **No Initiative / Program entity.** Projects are the top-level work container. No multi-Project rollup. No OKR-style goal grouping.
- **No Project Status Update entity.** Audit log of mutations + `comment_on_job` cover the human-readable narrative needs. No periodic-report layer.

---

## Stories (12, each parented to the cap #3 epic)

Each story carries: Objective, Scope (in/out), Why this matters (human outcome), Verification commands, DoD items table, Depends on, Submission shape.

### Story 3.1 — Schema migration for cap #3 entities

**Objective:** One Alembic migration `0004_cap03_entities` creates every table in §"Locked decisions for cap #3" item 2, plus the GIN and partial-btree indexes (items 3 + 4), plus the seeded contract profiles (item 12) and the seeded `ship-a-thing` Workflow (item 13). Round-trippable: `alembic upgrade head → downgrade -1 → upgrade head` produces identical schema.

**Why this matters (human outcome):** The data model that makes every later cap possible exists. The DB knows what a Project is, what a Workflow is, how Pipelines snapshot Workflows, how Jobs attach to Pipelines, how Labels route work. Without this row of bricks, nothing on top stands.

**Scope (in):** Migration file `apps/api/alembic/versions/0004_cap03_entities.py`. SQLAlchemy declarative models in `apps/api/src/aq_api/models/db.py`. Seeded `contract_profiles` rows (4) and seeded `workflows` + `workflow_steps` rows (1 workflow, 3 steps). Index DDL. CHECK constraints on the `jobs.state` enum and `job_edges.edge_type` enum.

**Scope (out):** No SQLAlchemy ORM relationships beyond what's needed for FK validation. No service layer yet. No routes.

**Verification:**
```
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
docker compose exec -T db psql -U aq -d aq2 -c "\dt"            # all 9 tables present
docker compose exec -T db psql -U aq -d aq2 -c "\d+ jobs"        # GIN index present
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM contract_profiles"   # = 4
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM workflow_steps WHERE workflow_id IN (SELECT id FROM workflows WHERE slug='ship-a-thing')"   # = 3
docker compose exec -T api uv run alembic -c apps/api/alembic.ini downgrade -1
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head    # idempotent
```

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-01 | All 9 tables created with correct CHECKs and FKs | `\d` output committed under `plans/v2-rebuild/artifacts/cap-03/schema-structure.txt` |
| DOD-CAP03-02 | `idx_jobs_labels_gin` GIN index present and used | `EXPLAIN` plan committed showing `Bitmap Index Scan on idx_jobs_labels_gin` |
| DOD-CAP03-03 | Migration round-trips (upgrade → downgrade -1 → upgrade) | `alembic-roundtrip.txt` committed |
| DOD-CAP03-04 | 4 Contract Profiles + 1 Workflow + 3 steps seeded | `psql` count outputs committed |

**Depends on:** Cap #2 schema (`actors`, `api_keys`, `audit_log`) on `main`.

**Submission shape:** Branch `aq2-cap-03` off main. First commit on the branch. No PR yet.

---

### Story 3.2 — Pydantic contract models for cap #3 entities

**Objective:** One Pydantic model file per entity, all `extra='forbid', frozen=True` per cap #1 lock. Models for Project, Label, Workflow, WorkflowStep, Pipeline, Job, JobEdge, JobComment, ContractProfile. Plus request/response shapes for every cap #3 op (CreateProjectRequest/Response, ListProjectsResponse, etc.). Re-exported from `aq_api.models`.

**Why this matters (human outcome):** Single source of truth. Every surface (REST, CLI, MCP) renders from these. No surface re-declares the contract. Cap #1's four-surface byte-equality guarantee continues to hold for cap #3 entities.

**Scope (in):** Pydantic v2 models. UTC datetime coercion via cap #1 `_datetime.parse_utc`. Field-level validation (slug regex, name length bounds, label name regex matching the registry constraint). Type tests asserting model dump round-trips.

**Scope (out):** No service layer. No routes. No CLI/MCP wiring (those are per-entity stories).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_models_cap03.py
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/models/
```

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-05 | All 9 entity models + request/response shapes have `extra='forbid', frozen=True` | grep output for `extra='forbid'` count matching expected |
| DOD-CAP03-06 | Mypy strict passes on new models | `mypy --strict` output |
| DOD-CAP03-07 | Round-trip serialization preserves all fields | pytest output |

**Depends on:** Story 3.1 (DB models inform Pydantic shapes).

**Submission shape:** Same branch, second commit.

---

### Story 3.3 — Project ops (create, list, get, update, archive)

**Objective:** 5 ops × 3 surfaces = 15 endpoints. REST under `/projects`, CLI as `aq project [verb]`, MCP as `create_project` etc. All mutations audited per cap #2. Slug uniqueness enforced. `archive_project` soft-deletes (sets `archived_at`); list excludes archived by default.

**Why this matters (human outcome):** A human can run `aq project create --name "AQ 2.0 Backlog"` and the rest of cap #3 has somewhere to attach to. Without Projects, Labels and Pipelines have no scope.

**Scope (in):** Service layer in `apps/api/src/aq_api/services/projects.py`. Routes in `apps/api/src/aq_api/routes/projects.py`. CLI in `apps/cli/src/aq_cli/main.py` (extend existing Typer app). MCP tools in `apps/api/src/aq_api/mcp.py`. Live tests covering happy path + all denial paths (slug collision = 409, missing project = 404, etc.).

**Scope (out):** No project-level metadata beyond name/slug/description. No Project members (Pact: API key = Actor identity, no team grouping). No project archive cascade — archiving a Project does NOT archive its Pipelines/Jobs (separate decision; cap #3 leaves child rows alone).

**Verification:** see common verification block at end.

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-08 | Create + list + get + update + archive on all three surfaces | parity test output |
| DOD-CAP03-09 | Slug uniqueness enforced; collision returns 409 with audit row | live test output |
| DOD-CAP03-10 | Archive soft-deletes; archived projects excluded from default list | live test output |

**Depends on:** Story 3.2 (models).

**Submission shape:** Third commit.

---

### Story 3.4 — Labels (register, attach, detach) with TEXT[] cache atomicity

**Objective:** 3 ops on Labels. `register_label` adds a row to the project-scoped `labels` registry. `attach_label` writes to `jobs.labels` TEXT[] (deduplicated, sorted) AND validates the label exists in the parent project. `detach_label` removes from the TEXT[]. All three audited.

**Why this matters (human outcome):** Labels are the routing primitive cap #4's `claim_next_job(label_filter)` will use. Get this wrong and every claim downstream is wrong. Atomic update of the GIN-indexed cache is non-negotiable.

**Scope (in):** Service + routes + CLI + MCP. Live concurrency test: two `attach_label` calls on the same Job from different actors; both labels end up in the TEXT[] without one clobbering the other (use `array_append` with FOR UPDATE row lock or single UPDATE with WHERE NOT label = ANY(labels)). Cross-project label attachment returns 403 `label_not_in_project` with audit row.

**Scope (out):** No bulk attach/detach (single label per call). No label rename (delete + recreate is the path).

**Verification:**
```
# Live concurrency assertion
docker compose exec -T api uv run pytest -q apps/api/tests/test_labels_concurrency.py
# GIN index actually used
docker compose exec -T db psql -U aq -d aq2 -c "EXPLAIN ANALYZE SELECT id FROM jobs WHERE labels @> ARRAY['area:web']"
```

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-11 | `attach_label` atomically updates registry-validation + TEXT[] cache | live concurrency test |
| DOD-CAP03-12 | Cross-project label attach returns 403 `label_not_in_project` with audit row | live test |
| DOD-CAP03-13 | GIN index used by labels @> filter | EXPLAIN plan committed |

**Depends on:** Story 3.3 (Projects must exist).

**Submission shape:** Fourth commit. **CHECKPOINT C1 fires here** — see hard checkpoints section.

---

### Story 3.5 — Workflow ops with versioning

**Objective:** 5 ops on Workflow. `create_workflow` creates v1 of a new `slug` family with N steps, each step carrying a `default_contract_profile_id` (per F-P0-rev2-1). `update_workflow` accepts a workflow id; **rejects with 409 `workflow_not_latest` if the id is not the highest non-archived version of its slug family** (per F-P1-rev2-4); on success, inserts a new row with the **same slug**, version+1, AND inserts a new set of `workflow_steps` (with their `default_contract_profile_id`) from the request payload tied to the new workflow_id (per F-P1-1 — old steps remain on the old workflow_id, untouched). `archive_workflow` accepts a slug and sets `is_archived=TRUE` on **ALL** rows in the family (per F-P0-5 — family-level archive, not per-version).

**Why this matters (human outcome):** The Workflow templates a human authors today don't break the Pipelines they instantiated yesterday. Every Pipeline carries a version pin (slug + version snapshotted at instantiate time); editing the template never silently mutates running work.

**Scope (in):** Service + routes + CLI + MCP. Workflow steps as nested Pydantic on create AND on update. Live test: create v1 with 3 steps → update_workflow with 4 different steps → confirm new row exists at v=2 with same slug, AND v=1 row + its 3 steps remain unchanged. Live test: archive_workflow(slug) → both v1 and v2 rows have `is_archived=TRUE`. Live test: instantiate against fully-archived family → 409 `workflow_archived`.

**Scope (out):** No Workflow step edges in this story (the JSONB column on `workflow_steps` exists from Story 3.1; cap #10 will add the edge-resolution semantics). No Workflow forking (deferred to cap #5+). No per-version archive (family-level only — by design per F-P0-5).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-14 | `update_workflow` inserts new workflow row + new workflow_steps from payload; old row + old steps untouched | live test diff query |
| DOD-CAP03-15 | Old versions remain queryable after `update_workflow` | live test |
| DOD-CAP03-16 | `archive_workflow(slug)` sets `is_archived=TRUE` on ALL rows in the family | live test asserting count of archived rows pre/post |
| DOD-CAP03-16b | `update_workflow(stale_id)` returns `409 workflow_not_latest` with audit row (per F-P1-rev2-4) | live test creates v1 → updates to v2 → tries to update v1's id → asserts 409 |
| DOD-CAP03-16c | Each `workflow_steps` row has a non-null `default_contract_profile_id` FK (per F-P0-rev2-1) | DB CHECK + live test |

**Depends on:** Story 3.3 (Projects).

**Submission shape:** Fifth commit.

---

### Story 3.6 — Pipeline ops (ad-hoc only, no instantiate)

**Objective:** 4 of the 5 Pipeline ops: `create_pipeline` (ad-hoc, no Workflow link), `list_pipelines`, `get_pipeline`, `update_pipeline`. The 5th — `instantiate_pipeline` — gets its own Story (3.7) because it's the most complex op in cap #3.

**Why this matters (human outcome):** Ad-hoc Pipelines exist for one-off work that doesn't need a Workflow template. The human can `aq pipeline create --project foo --name "hotfix-2026-04-28"` and start adding Jobs without scaffolding a Workflow first.

**Scope (in):** Service + routes + CLI + MCP. Pipeline must belong to a Project (project_id NOT NULL).

**Scope (out):** No `instantiate_pipeline` (Story 3.7). No Pipeline archive (Pipelines transition through their Jobs reaching terminal states; archiving a Pipeline is conflated with all-Jobs-cancelled, deferred decision).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-17 | Ad-hoc Pipeline created without Workflow link | live test |
| DOD-CAP03-18 | `instantiated_from_workflow_id` is NULL on ad-hoc Pipelines | DB query output |

**Depends on:** Story 3.3 (Projects).

**Submission shape:** Sixth commit.

---

### Story 3.7 — `instantiate_pipeline` snapshot semantics

**Objective:** The 5th Pipeline op. `POST /pipelines/from-workflow/{workflow_slug}` (per F-P0-5 — slug, not id) with body `{"project_id": UUID, "pipeline_name": str}` (per F-P0-rev2-1 — caller specifies destination project + Pipeline name; profile mapping flows from each step's `default_contract_profile_id`). Resolves the highest non-archived version of that slug family, creates a Pipeline row with `project_id` + `pipeline_name` + `instantiated_from_workflow_id` + `instantiated_from_workflow_version` set, then iterates `workflow_steps` for that version and inserts one Job per step **in `state='ready'`** (per F-P0-1) with `jobs.instantiated_from_step_id` set to the source step's id (per F-P0-2 — direct FK column on `jobs`, NOT a `job_edges` row), `jobs.contract_profile_id` set from the step's `default_contract_profile_id`, and `jobs.project_id` denormalized to match the new Pipeline's project. All inside one DB transaction. One audit row capturing `op='instantiate_pipeline'`, `target_kind='pipeline'`, `target_id=new_pipeline.id`, `request_payload={"workflow_slug": ..., "workflow_version": ..., "project_id": ..., "pipeline_name": ...}`, `response_payload={"job_count": N, "job_ids": [...]}`.

**Why this matters (human outcome):** This is the moment where templates become work. Run `aq pipeline instantiate --workflow ship-a-thing` and three Jobs appear in `ready` immediately claimable, each carrying a direct FK to the Workflow step they came from. The version pin means editing `ship-a-thing` v1 to v2 doesn't reach back and rewrite the Jobs already created.

**Scope (in):** Service + route + CLI + MCP. Live tests: success path (3 Jobs created in `ready` with `instantiated_from_step_id` and `contract_profile_id` from each step's default both set); Workflow family fully archived → 409 `workflow_archived`; Workflow slug not found → 404; project_id missing → 422 (Pydantic); pipeline_name missing → 422; concurrent instantiate of same Workflow → both succeed with separate Pipelines (no race). Audit row captures full snapshot fingerprint.

**Scope (out):** No partial instantiation (all-or-nothing). No "instantiate from version N" — always uses highest non-archived version of the slug family. Post-instantiate Job mutation goes through Story 3.8/3.9. **No `instantiated_from` rows in `job_edges`** — that table is Job-to-Job only.

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-19 | One transaction creates Pipeline + N Jobs (state=ready) + 1 audit row. Zero `job_edges` rows written by this op. | live test asserts row counts pre/post |
| DOD-CAP03-20 | Failure mid-transaction rolls back everything | monkeypatch test (force step-insert failure → no Pipeline, no Jobs, no audit row) |
| DOD-CAP03-21 | `instantiated_from_workflow_version` matches highest non-archived row at time of instantiate | live test diffs Pipeline row vs Workflow family |
| DOD-CAP03-22 | Each new Job's `instantiated_from_step_id` points to the source step AND `contract_profile_id` matches step's `default_contract_profile_id` | DB query joining jobs ↔ workflow_steps |
| DOD-CAP03-23 | All instantiated Jobs are in `state='ready'` (not `draft`) | live test asserts state |
| DOD-CAP03-23b | Composite FK `(pipeline_id, project_id)` enforces denormalization consistency | live test attempts to insert Job with wrong project_id and is rejected by DB |

**Depends on:** Story 3.5 (Workflows) + Story 3.6 (Pipelines).

**Submission shape:** Seventh commit.

---

### Story 3.8 — Job CRUD (create, list, get, update — metadata only)

**Objective:** 4 of the 7 Job ops. `create_job` (must bind to a Pipeline + Contract Profile by id per F-P0-4), `list_jobs` (filterable by project_id, pipeline_id, state), `get_job`, `update_job` (per F-P1-5 — accepts ONLY `title` and `description`; REJECTS state, claimed_*, and labels).

**Why this matters (human outcome):** Jobs are the unit of work. Cap #3 lets a human create them, list them, look at one, edit the title. Everything else (claim, submit, comment, cancel, ready-list) layers on top.

**Scope (in):** Service + routes + CLI + MCP. `create_job` defaults state to `'ready'` (capabilities.md: "Jobs go directly to `ready` on creation") — applies to both ad-hoc creates AND `instantiate_pipeline` per F-P0-1. `update_job` rejects state-writing payloads with 400 `cannot_write_state_via_update` and rejects label payloads with 400 `cannot_write_labels_via_update`; both denials audit-logged with the rejected field name.

**Scope (out):** No `comment_on_job` / `cancel_job` / `list_ready_jobs` here (those are 3.9, 3.9, 3.10). No label mutation through `update_job` — Story 3.4's `attach_label`/`detach_label` is the canonical path per F-P1-5.

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-24 | `update_job` with `state` field returns 400 `cannot_write_state_via_update` with audit row | live test |
| DOD-CAP03-25 | `update_job` with `labels` field returns 400 `cannot_write_labels_via_update` with audit row | live test |
| DOD-CAP03-26 | `create_job` ALWAYS creates Jobs in `state='ready'` (no `draft` entry path in cap #3) | live test |
| DOD-CAP03-27 | `list_jobs` paginates and filters by project_id, pipeline_id, state | live test |

**Depends on:** Story 3.6 (Pipelines).

**Submission shape:** Eighth commit.

---

### Story 3.9 — `comment_on_job` + `list_job_comments` + `cancel_job`

**Objective:** Three ops grouped (per F-P1-rev2-6 — `list_job_comments` added). `comment_on_job` writes to `job_comments` table; audit row records body length only (not body text — keeps audit_log small). `list_job_comments(job_id, limit, cursor)` is read-only, never audited, paginated FIFO by `created_at, id`, `limit <= 100`. `cancel_job` transitions any non-terminal Job to `cancelled` with audit row; from terminal states returns 409 `already_terminal`.

**Why this matters (human outcome):** Comments give humans + agents a place to leave durable notes on Jobs (the audit log captures mutations; comments capture intent). The list op makes them readable — without it, comments would be write-only. Cancel gives the operator the explicit "this work is no longer needed" path.

**Scope (in):** Service + routes + CLI + MCP for all three ops. Comment body validated for length bounds (1 ≤ len ≤ 16384) and rejects null bytes. `get_job` does NOT include comments inline — `list_job_comments` is the canonical read path. Cancel from each non-terminal state covered by separate test cases.

**Scope (out):** No comment editing or deletion (immutable history). No threaded comments (flat list ordered by created_at).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-28 | `comment_on_job` audit_log row has only `body_length` not body text | live test asserting audit_log JSONB shape |
| DOD-CAP03-29 | `cancel_job` from `done`/`failed`/`cancelled` returns 409 with audit row | live test |
| DOD-CAP03-30 | Comment body length bounds enforced (1 ≤ len ≤ 16384, no null bytes) | live test |
| DOD-CAP03-30b | `list_job_comments` returns paginated FIFO and is never audited (per F-P1-rev2-6) | live test asserting cursor stability + audit_log count delta = 0 after 100 calls |

**Depends on:** Story 3.8 (Jobs).

**Submission shape:** Ninth commit.

---

### Story 3.10 — `list_ready_jobs` read op (the AQ2-36 amendment)

**Objective:** `GET /jobs/ready?project={uuid}&label=...&limit=...&cursor=...`, `aq jobs ready --project ...`, MCP `list_ready_jobs(project_id, label_filter, limit, cursor)`. **`project_id` is REQUIRED** (per F-P1-rev2-5 — symmetric with cap #4 claim). Returns Jobs in `state='ready'` ordered FIFO by `(created_at, id)`, filtered by `project_id = :project_id AND labels @> :label_filter` (if labels provided). Pagination via opaque cursor (base64-encoded `{"created_at": "...", "id": "..."}`). `limit <= 100`. **Read-only, NEVER audited** (matches cap #2 reads-not-audited lock).

**Why this matters (human outcome):** An MCP-connected agent (or Mario) can preview the claim queue before deciding to claim. The agent passes `label_filter=["area:web"]`; AQ returns the FIFO-ordered set of `ready` Jobs whose labels include `area:web`. Same filter semantics cap #4's `claim_next_job` will use — getting the contract right HERE means cap #4 inherits the right behavior.

**Scope (in):** Service + route + CLI + MCP. Live test asserts FIFO order. Live test asserts `EXPLAIN` plan uses `idx_jobs_labels_gin`. Live test asserts NO audit row written after 100 read calls. Pagination cursor stable across DB writes (cursor encodes the boundary tuple, new Jobs created after cursor issue still appear on next page if they sort after the boundary).

**Scope (out):** No filtering by Workflow/Pipeline (project + labels are sufficient for v1). No sort customization (FIFO only).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-31 | FIFO order preserved within filter scope | live test diff |
| DOD-CAP03-32 | GIN index `idx_jobs_labels_gin` used by `labels @>` filter | EXPLAIN plan output |
| DOD-CAP03-33 | Zero audit rows after 100 list_ready_jobs calls | live test asserting `audit_log` count delta |
| DOD-CAP03-34 | Cursor pagination stable across new inserts | live test inserting Jobs mid-pagination |

**Depends on:** Story 3.4 (Labels) + Story 3.8 (Jobs).

**Submission shape:** Tenth commit.

---

### Story 3.11 — Contract Profile discovery (list + describe)

**Objective:** 2 read ops. `list_contract_profiles` returns the 4 seeded profiles. `describe_contract_profile(name)` returns the full schema + required_dod_ids for one profile. **Read-only, never audited.**

**Why this matters (human outcome):** A human (or agent) about to call `create_job` needs to know which Contract Profile to bind. `aq profile list` shows the four options; `aq profile describe coding-task` shows what DoD fields will be required at submit time (cap #5). Without this discoverability, agents would have to guess.

**Scope (in):** Service + routes + CLI + MCP. Live test asserts all 4 seeded profiles surface. Live test asserts profile schema is well-formed JSON.

**Scope (out):** No profile creation or update (cap #5).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-35 | All 4 seeded profiles returned | live test |
| DOD-CAP03-36 | `describe_contract_profile` returns required_dod_ids array | live test |
| DOD-CAP03-37 | Zero audit rows after profile reads | live test |

**Depends on:** Story 3.1 (seeded profiles in migration).

**Submission shape:** Eleventh commit.

---

### Story 3.12 — Parity tests + CI updates + atomicity coverage + redact-evidence

**Objective:** Mirror cap #2 Story 2.12. Add cap #3 entities to `tests/parity/`. Update `tests/parity/four_surface_diff.py` to cover new ops. Add atomicity tests for the multi-row mutations (`instantiate_pipeline`, `attach_label`). Update `scripts/redact_evidence.py` if needed for new patterns. Update CI workflows if any new env vars are introduced (cap #3 introduces NONE — all envs from cap #2 carry forward).

**Why this matters (human outcome):** The four-surface pact holds for cap #3. Any drift between REST and CLI and MCP is caught mechanically. The atomicity tests prove that complex mutations like `instantiate_pipeline` are all-or-nothing under failure injection.

**Scope (in):** Parity tests for every cap #3 op. Atomicity tests for `instantiate_pipeline` (force mid-transaction failure → assert no Pipeline, no Jobs, no edges, no audit row). Atomicity test for `attach_label` (force TEXT[] update failure after registry validation → assert TEXT[] not partially updated). Updated CI workflows. Updated `validate-cap03.sh` and `validate-cap03.ps1` runners.

**Scope (out):** No new Web tests (cap #3 ships no new Web views; Web parity tests from cap #2 continue to pass unchanged).

**DoD items:**
| ID | Statement | Evidence |
|---|---|---|
| DOD-CAP03-38 | Parity tests pass for all 27 cap #3 ops | parity-test-report.xml |
| DOD-CAP03-39 | Atomicity tests pass for `instantiate_pipeline` and `attach_label` | atomicity-test.xml |
| DOD-CAP03-40 | Full Docker test matrix passes (cap #2's 75 + cap #3's new tests) | committed pytest output |
| DOD-CAP03-41 | gitleaks scan clean on full history | gitleaks-history-pass.txt |

**Depends on:** Stories 3.1–3.11.

**Submission shape:** Twelfth commit. **CHECKPOINT C2 fires here** — see hard checkpoints section.

---

## MCP richness for cap #3

Carries forward cap #2's locked pattern (FastMCP-spec: server instructions, tool annotations, descriptions, output content bundling, input-schema field descriptions). Cap #3-specific additions:

1. **Tool annotations:**
   - `create_project`, `update_project`, `archive_project` → `destructiveHint: false` (additive)
   - `register_label`, `attach_label`, `detach_label` → `destructiveHint: true` for detach, `false` for register/attach
   - `create_workflow`, `update_workflow` → `destructiveHint: false`; `archive_workflow` → `destructiveHint: true`
   - `create_pipeline`, `instantiate_pipeline`, `update_pipeline` → `destructiveHint: false`
   - `create_job`, `update_job`, `comment_on_job` → `destructiveHint: false`
   - `cancel_job` → `destructiveHint: true, idempotentHint: false`
   - `list_*`, `get_*`, `describe_*`, `list_ready_jobs`, `list_contract_profiles` → `readOnlyHint: true`
2. **Output content bundling on `instantiate_pipeline`:** structured Pipeline + structured Jobs[] + a `text` block "Instantiated Pipeline X from Workflow Y v{version}. {N} Jobs created in `state='ready'` and immediately claimable. Each Job's `contract_profile_id` was set from its source step's default profile. Required next: optionally `attach_label` for routing, or hand off to cap #4's `claim_next_job`." (per F-P1-rev2-3 — text now matches the F-P0-1 ready-on-creation lock)
3. **Server instructions extension:** add the cap #3-specific guidance: "Jobs created via `create_job` AND `instantiate_pipeline` ALL start in `state='ready'` and are immediately claimable (per F-P0-1). The `draft` state is reserved for cap #10's `gated_on` mechanism and is not entered in cap #3. Use `attach_label` to add labels and `cancel_job` to withdraw work."

---

## Hard checkpoints

- **C1 — after Story 3.4 lands.** Foundational entities (Projects + Labels) live with audit, parity, and concurrency tests passing. Codex stops, posts evidence on the cap #3 epic, awaits Ghost approval before Story 3.5.
- **C2 — after Story 3.12 lands.** All 12 stories complete. Full Docker stack healthy. 27 ops covered by parity tests. Atomicity tests for `instantiate_pipeline` + `attach_label` green. Codex stops, posts evidence, awaits Ghost approval before opening PR.
- **PR open + Ghost merge approval.** Codex opens ONE PR. Awaits Ghost merge approval. Does NOT self-merge.

---

## Capability-level DoD list

The 45 DoD items embedded in stories 3.1–3.12 above (rev 3 added DOD-CAP03-16b, 16c, 23b, 30b for the rev 2 audit findings) plus these capability-wide DoDs:

| ID | Statement | Verification | Evidence |
|---|---|---|---|
| DOD-CAP03-42 | All 27 ops surface on REST + CLI + MCP with byte-equal payloads | parity test | `four-surface-equivalence.txt` |
| DOD-CAP03-43 | Cap #1 + Cap #2 tests still pass unchanged | pytest | `cap01-cap02-regression.txt` |
| DOD-CAP03-44 | `COMMIT_MATCHES_HEAD` invariant against authenticated `/version` on cap-03 branch tip | command | `commit-matches-head.txt` |
| DOD-CAP03-45 | Argon2 / HMAC / advisory-lock locks from cap #2 still present (sanity grep) | grep | `cap02-locks-still-present.txt` |
| DOD-CAP03-46 | No `/audit` Web view introduced (Pact lock) | grep | `web-routes.txt` |
| DOD-CAP03-47 | Cap #3 introduces zero new env vars (all carry forward from cap #2) | command | `env-diff.txt` |
| DOD-CAP03-48 | No Job ever enters `state='draft'` in cap #3 (per F-P0-1) | DB query asserting `SELECT count(*) FROM jobs WHERE state='draft'` = 0 across all live tests | `no-draft-jobs.txt` |
| DOD-CAP03-49 | `job_edges` contains zero rows with `edge_type='instantiated_from'` (per F-P0-2 — that lives on `jobs.instantiated_from_step_id`) | DB query | `job-edges-shape.txt` |

---

## Validation summary

Run `scripts/validate-cap03.sh` end-to-end. The script:
1. `docker compose down --remove-orphans && docker compose build --no-cache && docker compose up -d --wait`
2. `alembic upgrade head` then verify all 9 tables present.
3. Bootstrap a founder via `aq setup`.
4. Walk the entity graph: create Project → register two Labels → create Workflow `ship-a-thing` slug with three steps (or use the seeded one) → `instantiate_pipeline` against the slug → confirm 3 Jobs in `state='ready'` (per F-P0-1) each with `instantiated_from_step_id` populated (per F-P0-2 — direct FK on jobs, NOT a job_edges row) → update one Job's title via `update_job` → confirm `update_job` rejects state and labels payloads with audit-logged 400s (per F-P1-5) → `attach_label` two Labels to that Job → `list_ready_jobs` filtered by one Label and confirm the Job appears (per F-P1-7 — works now since instantiate creates `ready` Jobs) → comment on the Job → cancel one Job → `update_workflow` to add a step (creates v2 of the slug family per F-P0-5; v1 unchanged) → `archive_workflow(slug)` → confirm subsequent `instantiate_pipeline` against the slug returns 409 `workflow_archived` → query the audit log → confirm every mutation appears with the right `op` and `error_code`, zero reads audited.
5. Run all Docker pytest suites (`apps/api/tests`, `apps/cli/tests`, `tests/parity`).
6. `EXPLAIN` the GIN-index path on `labels @>` and the partial-btree path on `state='ready'`.
7. gitleaks v8.30.1 full-history scan.
8. `redact-evidence.sh` over every artifact before commit.

---

## Submission shape

- **Single branch** `aq2-cap-03` off `main` at `dc4ad37`.
- **Story-by-story commits** (12 commits, each story = one commit).
- **ONE PR at the end.** Codex stops at C2 for Ghost evidence review, then opens one PR.
- **Each story closes its child ticket** via `plane_update_status` with closeout comment.
- **Strict ADR-AQ-030 evidence per story** under `plans/v2-rebuild/artifacts/cap-03/`, redacted via `scripts/redact-evidence.sh` before commit.
- **Hard preconditions** (AQ2-21, AQ2-36, AQ2-13) all on main as of 2026-04-27 13:58 UTC; cap #3 may begin immediately when Mario queues it.

---

## Risks / deviations (declared in submission)

1. **`workflow_steps.step_edges` shape decision deferred to Story 3.1 implementation.** JSONB embed vs. separate `workflow_step_edges` table — both are viable. Story 3.1 picks one and pins it in the migration; the choice does not affect any downstream story since cap #3 doesn't expose Workflow-step edges via API.

2. **`labels` registry-junction collapse decision deferred to Story 3.4.** The `jobs.labels` TEXT[] cache is locked. Whether a separate junction table backs it (with strict referential integrity) or the TEXT[] is the only storage (with name validation at write time) is a Story 3.4 decision. Either path satisfies the contract `list_ready_jobs(label_filter)` expects.

3. **`update_job` rejection of state + label writes is enforced at the service layer**, not via Pydantic schema (the request model accepts the field; the service rejects it with 400 + audit row). This is so the audit row captures WHAT was rejected, which Pydantic-level rejection can't do without raising a generic 422.

4. **`comment_on_job` audit row stores body_length only, not body text.** Bodies live in `job_comments`. This is a deliberate divergence from "audit log captures the request_payload" — bodies could be large, and `audit_log` is meant to stay query-friendly. Acknowledged deviation; cap #3 plan explicitly documents this.

5. **Workflows and Contract Profiles ship GLOBAL in v1, not project-scoped (per F-P1-2).** capabilities.md's customization-line table says they're "Customizable per Project," but cap #3 ships them as global tables (no `project_id`). This is a declared deviation; project-scoping deferred to cap #5 (when Contract Profile authoring lands) or a later capability. Pipelines and Labels remain project-scoped.

6. **Cap #3 never enters `state='draft'` (per F-P0-1).** All Jobs start in `ready` regardless of creation path (`create_job` or `instantiate_pipeline`). The `draft` state is locked into the schema but reserved for cap #10's `gated_on` mechanism. DOD-CAP03-48 enforces this via DB query asserting zero `draft` Jobs across all live tests.

7. **`instantiated_from` is a direct FK on `jobs`, not a `job_edges` row (per F-P0-2).** `job_edges` is strictly Job-to-Job for `gated_on`, `parent_of`, `sequence_next`. The Job-to-Workflow-step relationship lives on `jobs.instantiated_from_step_id`.

8. **Workflow archive is family-level, not per-version (per F-P0-5).** `archive_workflow(slug)` archives all rows in the slug family. This is intentional: per-version archive produces ambiguous "latest non-archived" semantics. If a future capability needs per-version archive, file a new ticket; do NOT reopen this lock.

9. **Cycles, Initiatives, Project Status Updates explicitly forbidden** per Mario's 2026-04-27 gap-analysis decision. Re-litigating these is out of scope; if a future capability needs them, file a NEW capability slot, do NOT reopen cap #3.

10. **`create_actor` declared deviation from cap #2 carries forward.** Cap #2 mints API keys via `create_actor` (REST/CLI/MCP) because there's no UI yet. This is a known deviation from "API keys are minted in the UI by a human only." Cap #11 (UI) will reverse it. Cap #3 does not introduce additional key-minting paths.

11. **`review_complete` transition has no capability owner.** capabilities.md line 27 lists `review_complete` as a transition op, but no capability assigns ownership. Out of cap #3 scope. Filing as a follow-up gap-ticket post cap #3 merge.

---

## Ready for execution

Rev 3 incorporates all 20 findings across two audit cycles:
- **Rev 1 audit (Gemini + Codex, 2026-04-27)** — 12 findings folded into rev 2: F-P0-1 through F-P0-5 (draft→ready, job_edges polymorphism, seeded created_by_actor_id, Contract Profile versioning, Workflow version/archive identity) + F-P1-1 through F-P1-7.
- **Rev 2 re-audit (Codex, 2026-04-27)** — 8 new findings folded into rev 3: F-P0-rev2-1 (instantiate_pipeline underspecified — fixed via `workflow_steps.default_contract_profile_id` + request body `{project_id, pipeline_name}`) + F-P1-rev2-1 through F-P1-rev2-7.
- **Gemini rev 2 verdict: PASS** (100% — "the plan is airtight").

Codex's rev 2 estimate: **78–82% as written** (after rev 2's only P0 blocker landed); rev 3 closes that → **92–95%** range per his framing. Story-by-story execution discipline: Codex executes one story at a time, runs that story's verification commands, commits, then moves to the next. C1 (after Story 3.4) and C2 (after Story 3.12) are hard stops for `claude` audit before continuing. PR-open + Ghost merge approval is the final gate (no self-merge). This matches the cap-02 cadence that delivered cleanly.

Rev 3 plan is locked. Codex may begin Story 3.1 the moment Mario queues AQ2-21's successor epic in Plane.
