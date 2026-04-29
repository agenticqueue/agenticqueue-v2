# Plan: AQ 2.0 Capability #4 — A Job can be claimed atomically

## Context

Cap #1 (four-surface ping), cap #2 (Authenticated Actors + Bearer auth + same-transaction audit log), cap #3 (entity CRUD with seeded `ship-a-thing` template Pipeline), and cap #3.5 (Workflow→Pipeline collapse + Contract Profile drop + inline `contract` JSONB) are all on `main` at `c956f1d`. AQ2 has Projects, Pipelines (templates and runs), and Jobs sitting in `state='ready'` — `list_ready_jobs` previews the queue, but no agent can pick a Job up. **Cap #4 closes the first half of the loop:** atomic claim with FIFO + label filter + project scope, plus the three companion ops that manage claim lifetime (`release_job`, `reset_claim`, `heartbeat_job`) and a background sweep that auto-releases stuck `in_progress` Jobs.

This plan is **rev 1**, written after gate-1 (brief approved with 12 corrections from Codex) and gate-2 (pre-plan approved with 7 tweaks) on 2026-04-28. Every locked decision below is intentional and grep-verifiable; every story carries a "Why this matters (human outcome)" line; every DoD has a real verification command and an artifact path.

Cap #4 ships **4 ops** spanning REST + CLI + MCP. Web tier is untouched per the Pact (cap #11 owns UI). The schema delta is minimal: one new partial index, one reserved system actor row, and zero new tables (the `claim_heartbeat_at` column already lives on `jobs` from cap-3 Story 3.1, gated by `update_job`'s `CLAIM_UPDATE_FIELDS` rejection set until cap #4 unlocks the write paths).

### Findings folded in from gate-2 audit (Codex, 2026-04-28)

**Locked corrections — these are not re-litigated below; they shape the plan:**

- **C-1** Sweep is in-process asyncio coroutine wrapping `run_claim_auto_release_once(now: datetime)`; tests call the function directly with mocked timestamps, never sleep.
- **C-2** Reserved system actor `kind='script', name='aq-system-sweeper'` created idempotently at app startup AND seeded by migration. First sweep can never fail on missing actor.
- **C-3** `claim_auto_release` audit row: `target_kind='job', target_id=job_id`. Previous claimant's `actor_id` lives in `request_payload.previous_claimant_actor_id`, NOT in `target_id`.
- **C-4** Successful `heartbeat_job` calls do NOT write audit rows (lease maintenance, not business history). Only denials audit. Documented deviation from cap-2's "every mutation audits" rule.
- **C-5** Context Packet stub shape: `{project_id, pipeline_id, current_job_id, previous_jobs: [], next_job_id: null}`. No Contract Profile fields. MCP next-step text references the Job's inline `contract` field, not `describe_contract_profile`.
- **C-6** Two narrow partial indexes: existing `idx_jobs_state_project_created` (partial WHERE `state='ready'`) for claim path; new `idx_jobs_in_progress_heartbeat` (partial WHERE `state='in_progress'`) for sweeper.
- **C-7** Two env vars only: `AQ_CLAIM_LEASE_SECONDS=900` `[60, 86400]` and `AQ_CLAIM_SWEEP_INTERVAL_SECONDS=60` `[5, 3600]`. No alternate names. Heartbeat cadence is MCP-text guidance, not a server env var.
- **C-8** Claim query JOINs `pipelines`, excludes `is_template=true` AND `archived_at IS NOT NULL` — mirrors `list_ready_jobs` exactly.
- **C-9** CLI under `aq job` group: `aq job claim`, `aq job release`, `aq job reset-claim`, `aq job heartbeat`. No top-level `aq claim` / `aq release` aliases.
- **C-10** No-ready-job → `409 no_ready_job`, audited (target_id NULL — denied attempts have no Job to point at).
- **C-11** `release_job`, `reset_claim`, `claim_auto_release` all clear `claimed_by_actor_id`, `claimed_at`, `claim_heartbeat_at`. `heartbeat_job` updates only `claim_heartbeat_at`.
- **C-12** Race tests: 50 concurrent claimers, no duplicate winners, exactly one audit row per success, denial audits well-formed.

**Tweaks from gate-2 (these are the rev-1 deltas):**

- **T-1** `audited_op` refactor uses **four-path semantics** with `skip_success_audit: bool = False`. The skip-success path commits the mutation (no audit row). See Locked Decision 4.
- **T-2** `run_claim_auto_release_once(now=...)` owns its actor context — sets the `authenticated_actor_id` contextvar internally with try/finally reset. Direct tests don't depend on a request context being present.
- **T-3** Parity timing: **Option A (incremental)** — each API-surface story regenerates `tests/parity/openapi.snapshot.json` and `tests/parity/mcp_schema.snapshot.json` for its own ops. C1 after Story 4.2 requires parity green. Story 4.6 becomes "MCP richness + race + atomicity," not snapshot regeneration.
- **T-4** `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` range locked: `[5, 3600]`, default `60`.
- **T-5** `no_ready_job` audit shape locked (target_id NULL; `request_payload` carries the inputs; `response_payload` carries the error code).
- **T-6** Per-Job atomicity invariant for the sweep — each released Job + its `claim_auto_release` audit row commit in one transaction. Batch failures leave the invariant intact.
- **T-7** Error-code lock table for terminal/non-`in_progress` cases (see Locked Decision 11).

### Why cap #4 matters (human outcome)

After cap #4 ships, AQ 2.0 supports the **"pull, do not push"** primitive that everything downstream depends on. The human or agent can:
- Run `aq job claim --project foo --label area:web` and atomically receive one `ready` Job to work on, with FIFO ordering inside the label filter scope.
- See exactly-one-winner semantics under concurrency: 50 agents racing for one Job produces one winner and 49 clean `409 no_ready_job` denials — never a duplicate claim.
- Refresh the claim's lease via `aq job heartbeat` while working (recommended ~30s cadence; server enforces only the 15-minute lease via `AQ_CLAIM_LEASE_SECONDS`).
- Voluntarily return work to `ready` via `aq job release`, or recover a stuck claim from any actor via `aq job reset-claim --reason "..."`.
- Trust that crashed agents' work returns to `ready` automatically: the in-process sweep flips `in_progress` Jobs whose heartbeat is older than the lease back to `ready`, with a `claim_auto_release` audit row attributed to the reserved `aq-system-sweeper` actor.

What cap #4 deliberately does **not** ship: submission validation (cap #5), gated_on auto-resolution (cap #10), real Context Packet content (cap #8 — cap #4 ships an empty-stub Packet for forward-compat), UI views (cap #11), agent-capability registry (forbidden — routing is caller-side via labels per AQ2-36).

---

## Hard preconditions (must be on `main` before cap #4 first commit)

| Ticket | Title | Status |
|---|---|---|
| AQ2-39 | Capability #3 epic | **DONE** ✓ |
| AQ2-54 | Capability #3.5 epic | **DONE** ✓ |
| AQ2-36 | capabilities.md cap #3 + #4 amendments | **DONE** ✓ (`6841155`) |
| AQ2-16 | Claim orphaning gap-ticket | folds into Story 4.5 (heartbeat/sweep) |
| AQ2-17 | Covering index gap-ticket | folds into Story 4.1 (schema delta) |

Cap-3 + cap-3.5 squash-merge to `main` at `c956f1d` is the cap-4 starting point. No additional preconditions.

---

## Capability statement (verbatim from `capabilities.md`, with pending fix-up note)

> **Capability #4: A Job can be claimed atomically.** Two Actors race to claim the same `ready` Job; exactly one wins (Job transitions to `in_progress` with the claimant set), the other gets a 409 Conflict; claimants can release; any Actor can `reset_claim` a stuck `in_progress` Job with a reason.

A surgical fix-up commit lands alongside Story 4.7 to amend the cap-4 prose in `capabilities.md` for stale CLI shorthand, the `describe_contract_profile` reference (op was cancelled in cap-3.5 / AQ2-50), the heartbeat-audit policy, the `claim_auto_release` `target_id` clarification, and the `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` addition. See "Risks / deviations" item 1 for exact line ranges.

**Depends on:** Cap #2 (Bearer auth + audit log) and Cap #3 + Cap #3.5 (Jobs must exist to be claimed; `claim_heartbeat_at` column already on `jobs`).

---

## Locked decisions for cap #4

These are cap #4-specific commitments **beyond** what cap #1, cap #2, cap #3, and cap #3.5 already locked. Every story below honors all of them.

1. **One Alembic migration revision** (`0006_cap04_indexes_and_system_actor`) ships the entire schema delta:
   - `CREATE INDEX idx_jobs_in_progress_heartbeat ON jobs (claim_heartbeat_at, id) WHERE state='in_progress'` — partial btree, sweeper's covering path.
   - **Idempotent system-actor seed using `INSERT ... SELECT ... WHERE NOT EXISTS`** (portable across the partial-unique index `actors_name_active_uniq (name) WHERE deactivated_at IS NULL`):
     ```sql
     INSERT INTO actors (name, kind)
     SELECT 'aq-system-sweeper', 'script'
     WHERE NOT EXISTS (
       SELECT 1 FROM actors
       WHERE name = 'aq-system-sweeper' AND deactivated_at IS NULL
     );
     ```
     Idempotent on fresh DB (one active row inserted) AND on existing DB (zero rows inserted if the active row already exists). If a deactivated row exists from a prior incarnation, this inserts a new active row alongside it (the partial-unique index permits multiple inactive rows + one active). Migration uses a SQL `op.execute(...)` with this exact text — does NOT use SQLAlchemy ORM inserts, to avoid model-import drift in migrations.
   - No new columns. No new tables. (`jobs.claim_heartbeat_at` already exists from cap-3 Story 3.1.)

2. **Concurrency mechanism is `SELECT ... FOR UPDATE SKIP LOCKED` in an explicit transaction.** Locked in capabilities.md cap-4 line 264; not advisory locks. The claim service opens an explicit transaction (`async with session.begin():` or its `audited_op`-managed equivalent), runs `SELECT id FROM jobs JOIN pipelines ... WHERE state='ready' AND project_id=:p AND pipelines.is_template=false AND pipelines.archived_at IS NULL [AND labels @> :label_filter] ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED`, then updates that row. SQLAlchemy 2 async session config must be verified to autobegin transactions; if it does not, the SKIP LOCKED hint is silently no-op'd.

3. **Two env vars locked, no alternates:**
   - `AQ_CLAIM_LEASE_SECONDS` — default `900` (15 min), validator-checked range `[60, 86400]`. Field on `_settings.py:Settings`.
   - `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` — default `60`, validator-checked range `[5, 3600]`. Field on `_settings.py:Settings`.
   - **No** `AQ_CLAIM_TIMEOUT_SECONDS`, `AQ_HEARTBEAT_INTERVAL_SECONDS`, `AQ_SWEEPER_RUN_INTERVAL_SECONDS`. AQ2-16 named those in the gap-ticket era; the AQ2-36 amendment to capabilities.md superseded them. One source of truth.
   - Heartbeat client cadence (~30s recommendation) is MCP `set_instructions` text, NOT a server env var.

4. **`audited_op` refactor — four-path semantics.** The cap-2 context manager (`apps/api/src/aq_api/_audit.py`) gains a `skip_success_audit: bool = False` keyword-only argument. The four paths:

   | Path | Mutation | Audit row | Final commit |
   |---|---|---|---|
   | success, normal audit (`skip_success_audit=False`) | committed | success audit recorded | one commit covers both |
   | success, `skip_success_audit=True` | committed | none | one commit covers mutation only |
   | `BusinessRuleException` (denial) | rolled back | denial audit recorded with `error_code` | one commit covers denial audit only |
   | unexpected exception | rolled back | none | re-raise after rollback |

   Heartbeat-success is the only call site shipping with `skip_success_audit=True` in cap #4. The deviation is documented as a code comment at the heartbeat service and as an inline rationale in `_audit.py:audited_op`'s docstring. Refactor footprint: ~5 lines of code; existing call sites carry the default `False` and behave identically to today.

5. **Heartbeat-success-not-audited deviation locked.** Successful `heartbeat_job` calls do NOT write audit rows (lease maintenance, not business history; the Job row already stores the only state AQ needs in `claim_heartbeat_at`). Heartbeat **denials** still audit. The cap-2 invariant ("every mutation route uses `audited_op`") is preserved by the `skip_success_audit=True` path — the heartbeat handler still enters the context manager.

6. **Auto-release sweep is an in-process asyncio coroutine.** Implementation chooses asyncio over `pg_cron` per locked correction C-1: cap #12's first-run install path stays free of Postgres extensions, and v1 is single-instance per the Pact (no multi-replica fan-out concern). The sweep is a thin loop:
   ```
   while True:
       await asyncio.sleep(settings.claim_sweep_interval_seconds)
       async with SessionLocal() as session:
           await run_claim_auto_release_once(session, now=datetime.now(UTC))
   ```
   The loop opens a fresh session per iteration (no long-held connections; no pool starvation under request-handler load). The function `run_claim_auto_release_once(session, *, now: datetime, system_actor_id: UUID | None = None)` is the **authoritative test surface** — direct tests pass a fixed `now` and never call `asyncio.sleep`. The function sets the `authenticated_actor_id` contextvar internally (resolving the system actor if `system_actor_id is None`) with try/finally reset so contextvar leakage is impossible.

7. **System actor wiring — hybrid (migration seed + startup safety valve).** The `0006_*` migration seeds the `aq-system-sweeper` row idempotently. App startup (in `app.py`'s lifespan) calls `ensure_system_actor(session) -> UUID`, which `SELECT`s by name + active state, and inserts if missing. This makes the bootstrap robust against partial migration state and against test-cleanup helpers that may DELETE FROM actors (cap-3.5's history includes one such helper bug fixed in AQ2-53). The system actor is never deactivated by application code; only manual ops (out of scope) can revoke it.

8. **Per-Job atomicity invariant for the sweep.** Each released Job's state reset (`state='ready'`, `claimed_by_actor_id=NULL`, `claimed_at=NULL`, `claim_heartbeat_at=NULL`) and its `claim_auto_release` audit row are committed in one transaction. A batch sweep (N stale Jobs) is N independent transactions. Failure mid-batch leaves earlier Jobs released-with-audit and later Jobs untouched — never released-without-audit, never audited-without-release. Dedicated atomicity test (Story 4.6 / `tests/atomicity/test_claim_auto_release_atomicity.py`) injects a flush failure on a Job mid-batch and asserts the invariant.

9. **Claim query mirrors `list_ready_jobs` JOIN exactly:**
   ```sql
   SELECT j.id FROM jobs j
   JOIN pipelines p ON j.pipeline_id = p.id
   WHERE j.state = 'ready'
     AND j.project_id = :project_id
     AND p.is_template = false
     AND p.archived_at IS NULL
     [AND j.labels @> :label_filter]
   ORDER BY j.created_at ASC, j.id ASC
   LIMIT 1
   FOR UPDATE SKIP LOCKED
   ```
   **Cap #3.5 template + cloned Pipeline Jobs are state='ready'** (verified at `apps/api/src/aq_api/services/pipelines.py:215` and migration `0005_cap0305_schema_consolidation` line 403; cap-3.5 evidence under `plans/v2-rebuild/artifacts/cap-0305/seed-template-pipeline.txt` confirms `all_ready: true`). They are excluded from claim/list queue behavior **only** by the JOIN to `pipelines` + `is_template=false` + `archived_at IS NULL` predicates — NOT by state filtering. **Cap #4 introduces zero draft Jobs and does not rely on draft state.** The state filter `j.state='ready'` is required for FIFO correctness, not for template exclusion. Mirrors `apps/api/src/aq_api/services/list_ready_jobs.py:29-77` exactly.

10. **Claim field clearing locked:**
    - `release_job`, `reset_claim`, `claim_auto_release` all NULL `claimed_by_actor_id`, `claimed_at`, `claim_heartbeat_at` AND set `state='ready'` (single UPDATE).
    - `heartbeat_job` updates only `claim_heartbeat_at = now()`. State, claimant, claimed_at unchanged.
    - `claim_next_job` sets `claimed_by_actor_id`, `claimed_at = now()`, `claim_heartbeat_at = now()`, `state='in_progress'` in one UPDATE.

11. **Error code lock table** (every cap-4 op + every state-mismatch case):

    | Op | Condition | Status | error_code | Audited? | DoD |
    |---|---|---|---|---|---|
    | `claim_next_job` | success | 200 | n/a | yes (success) | DOD-AQ2-S4.2-01 |
    | `claim_next_job` | no `ready` Job matches filter | 409 | `no_ready_job` | yes (denial; target_id=NULL) | DOD-AQ2-S4.2-04 |
    | `claim_next_job` | invalid `project_id` (Pydantic) | 422 | (Pydantic field validation) | not audited (request never reached service) | DOD-AQ2-S4.2-05 |
    | `release_job` | success | 200 | n/a | yes (success) | DOD-AQ2-S4.3-01 |
    | `release_job` | wrong claimant | 403 | `release_forbidden` | yes (denial) | DOD-AQ2-S4.3-04 |
    | `release_job` | not claimed / not `in_progress` | 409 | `job_not_claimed` | yes (denial) | DOD-AQ2-S4.3-05 |
    | `release_job` | Job not found | 404 | `job_not_found` | yes (denial) | DOD-AQ2-S4.3-06 |
    | `reset_claim` | success | 200 | n/a | yes (success) | DOD-AQ2-S4.3-07 |
    | `reset_claim` | not claimed / not `in_progress` | 409 | `job_not_claimed` | yes (denial) | DOD-AQ2-S4.3-08 |
    | `reset_claim` | missing/empty `reason` | 422 | (Pydantic field validation, matches cap-3 `update_job` convention) | not audited | DOD-AQ2-S4.3-09 |
    | `reset_claim` | Job not found | 404 | `job_not_found` | yes (denial) | DOD-AQ2-S4.3-10 |
    | `heartbeat_job` | success | 200 | n/a | **NO (per Locked Decision 5)** | DOD-AQ2-S4.4-01 |
    | `heartbeat_job` | wrong claimant | 403 | `heartbeat_forbidden` | yes (denial) | DOD-AQ2-S4.4-04 |
    | `heartbeat_job` | not `in_progress` | 409 | `job_not_in_progress` | yes (denial) | DOD-AQ2-S4.4-05 |
    | `heartbeat_job` | Job not found | 404 | `job_not_found` | yes (denial) | DOD-AQ2-S4.4-06 |
    | `claim_auto_release` (sweep) | success | n/a (background) | n/a | yes (success; `authenticated_actor_id` = system actor) | DOD-AQ2-S4.5-04 |
    | `claim_auto_release` (sweep) | system-actor missing at sweep time | n/a (logged + skipped) | n/a | not auditable (no actor) | DOD-AQ2-S4.5-08 |

12. **`no_ready_job` audit shape locked** (the only denial that has no Job row to target):
    ```
    op = 'claim_next_job'
    target_kind = 'job'
    target_id = NULL
    error_code = 'no_ready_job'
    request_payload = {project_id, label_filter, ...}
    response_payload = {error: 'no_ready_job'}
    ```
    The existing `audit_log_target_idx` (partial WHERE `target_id IS NOT NULL`) does NOT index this row — denied no-row attempts are query-able by `op + actor + ts`, which is correct for forensic reasoning ("show me every claim attempt by actor X that found no work").

13. **Context Packet stub shape locked** (forward-compat with cap #8):
    ```
    {
      "project_id": "<uuid>",
      "pipeline_id": "<uuid>",
      "current_job_id": "<uuid>",
      "previous_jobs": [],
      "next_job_id": null
    }
    ```
    No `contract_profile_name`, no `contract_id`, no real link content. `previous_jobs[]` and `next_job_id` are populated by cap #8 once `sequence_next` edges from cap #10 exist. This precedent matches cap-3's empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays for cap-9 forward-compat.

14. **CLI under `aq job` group only.** Four commands: `aq job claim`, `aq job release`, `aq job reset-claim`, `aq job heartbeat`. No top-level `aq claim` / `aq release` aliases. Resolves capabilities.md cap-4's `aq claim` / `aq release` shorthand to the cap-3 singular-group convention. Verified by inspection: `apps/cli/src/aq_cli/main.py` already groups job-related commands under `aq job` (cap-3 Story 3.8).

15. **Parity-test timing — Option A (incremental snapshot updates per story).** Stories 4.2, 4.3, 4.4 each regenerate `tests/parity/openapi.snapshot.json` and `tests/parity/mcp_schema.snapshot.json` for their own ops. C1 (after Story 4.2) requires parity green for `claim_next_job`. Story 4.6 ships **MCP richness refinement** (`set_instructions` server-level block + multi-part `claim_next_job` content list refinement) + **race + atomicity tests**, NOT snapshot regeneration. Each push has clean snapshots.

16. **`capabilities.md` fix-up commit** lands alongside Story 4.7 (the C2 evidence pack). Surgical edits to lines 264, 265, 267, 271, 272, 281, 290, 297. See "Risks / deviations" item 1 for exact replacement text.

17. **EXPLAIN evidence required** for three query shapes, committed under `plans/v2-rebuild/artifacts/cap-04/`:
    - `explain-claim-no-label.txt` — `claim_next_job` query without label filter, asserts `idx_jobs_state_project_created` use.
    - `explain-claim-with-labels.txt` — `claim_next_job` query with `labels @>` filter, asserts both `idx_jobs_state_project_created` AND `idx_jobs_labels_gin` use (or the planner's chosen combination).
    - `explain-sweeper-stale-claim.txt` — sweep query (`WHERE state='in_progress' AND claim_heartbeat_at < now() - lease`), asserts `idx_jobs_in_progress_heartbeat` use.

18. **MCP richness pattern locked from cap #4 forward** (carried into every later cap):
    - `mcp.set_instructions(...)` server-level block (brand-new wiring; FastMCP supports it but cap-3 didn't ship it).
    - Tool annotations: every cap-4 mutation gets `{"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False}`. (Existing reads in cap-1/2/3 use `{"readOnlyHint": True}`.)
    - Tool descriptions auto-derived from Pydantic field docstrings + a per-op "why-to-use / when-to-use" line authored at the MCP tool decorator.
    - `claim_next_job` returns a multi-part MCP content list: Job JSON + Packet stub JSON + natural-language text block.

19. **`ClaimNextJobResponse` carries structured lease facts** (Mario lock 4) so REST/CLI/MCP clients behave correctly without reading deployment config out-of-band:
    ```
    ClaimNextJobResponse {
      job: Job,
      packet: ContextPacketStub,
      lease_seconds: int,                       # = settings.claim_lease_seconds
      lease_expires_at: datetime,               # = job.claimed_at + lease_seconds (UTC, Z-form)
      recommended_heartbeat_after_seconds: int  # = 30 (module-level constant, NOT an env var)
    }
    ```
    `recommended_heartbeat_after_seconds = 30` is a module-level constant in `apps/api/src/aq_api/services/claim.py`, not an env var (per Locked Decision 3 — only two env vars in cap-4). Clients across all surfaces receive identical lease facts in the success response. The MCP `set_instructions` text continues to mention the ~30s recommendation as fallback documentation; structured clients should read `recommended_heartbeat_after_seconds` from the response.

20. **API startup is robust to transient `ensure_system_actor` failure** (Mario lock 5). On startup, the lifespan attempts `ensure_system_actor` once. On exception (transient DB unavailability, race), it logs a warning and proceeds with `system_actor_id = None`. The sweep coroutine then calls `ensure_system_actor` at the top of each iteration until success; the resolved UUID is cached for subsequent iterations. **API request handling is NOT taken down by transient sweep setup failure.** Auditability of mutations served by request handlers is unaffected (request mutations use the per-request authenticated actor, not the system actor). If the operator prefers fail-fast startup, that's a v1.1+ config flag — not the v1 default.

21. **Explicit transaction boundaries per mutating service** (Mario lock 6):
    - For `claim_next_job`, `release_job`, `reset_claim`, `heartbeat_job`: **one transaction contains the state mutation + the audit row.** No service manually commits inside an outer route transaction. The `audited_op` context manager owns commit/rollback.
    - `BusinessRuleException` rolls back the attempted state mutation and commits ONLY the denial audit row (existing `audited_op` behavior, preserved).
    - Heartbeat-success uses `audited_op(skip_success_audit=True)` per Locked Decision 4: state mutation commits; no audit row recorded; same single-commit transaction boundary.
    - For `claim_auto_release` (sweep): **one transaction per stale Job.** A batch of N stale Jobs is N independent transactions per Locked Decision 8. Auto-release success commits state mutation + audit row (with `error_code='lease_expired'`) atomically. Failure on any single Job rolls back THAT Job's transaction, leaves prior Jobs released-with-audit, leaves later Jobs untouched.

22. **MCP multi-part output preflight** (Mario lock 7). Story 4.6's first commit is a small spike: write a throwaway test that registers a FastMCP tool returning a `list[Content]` (or whatever the installed FastMCP version's exact return type is), runs the live MCP HTTP transport, and asserts the multi-part response shape arrives intact at a real MCP client. The spike confirms the installed FastMCP version's API matches the assumption in this plan. If the spike reveals a different return shape, the plan is amended via a one-line clarification in Story 4.6 BEFORE `claim_next_job`'s multi-part wiring is implemented. Spike output → `artifacts/cap-04/fastmcp-multipart-spike.txt`.

---

## Carry-forward locked rules from caps #1, #2, #3, #3.5

Every cap #4 story honors all of these. Repeated for grep-recall, not re-litigation.

**From cap #1:**
- Z-form datetime via `aq_api._datetime.parse_utc`. All timestamps timezone-aware UTC.
- Single Pydantic source of truth — no surface re-declares contract.
- Real-stack validation: `docker compose down && up -d --build --wait` + `_assert_commit_matches_head()`.
- Strict ADR-AQ-030 evidence — every artifact under `plans/v2-rebuild/artifacts/cap-04/`, redacted via `scripts/redact-evidence.sh` before commit.
- Four-surface parity discipline: REST + CLI + MCP + Web (Web no-op for cap #4 since no new views).

**From cap #2:**
- All API + MCP handlers `async def`. Never sync.
- Postgres 16-alpine, internal-only network, `aq2_pg_data` named volume.
- SQLAlchemy 2.x async (asyncpg) at runtime; psycopg sync for Alembic only.
- In-process service layer: REST + MCP handlers call same Python service functions.
- MCP HTTP requires caller's Bearer; no bridge actor; `agent_identity` decorative-only.
- Reads NEVER audited; mutations ALWAYS use `audited_op` (cap-4 extends with `skip_success_audit=True` for heartbeat-success, but the call site still enters the context manager).
- Same-transaction audit guarantee: `audited_op` context manager (refactored in cap-4 Story 4.4).
- Three-layer secret redaction: regex-recursive in app + `scripts/redact-evidence.sh` for artifacts + gitleaks workflow.
- HMAC-SHA256 lookup_id is the auth lookup primitive.
- All evidence committed under `plans/v2-rebuild/artifacts/cap-NN/`, redacted before commit.

**From cap #3 + cap #3.5:**
- Pipelines are one entity type with `is_template` BOOLEAN distinguishing templates from runs; archived via `archived_at` timestamp.
- **Cap #3.5 template + cloned Pipeline Jobs are created in `state='ready'`** (per shipped cap-3.5 implementation at `services/pipelines.py:215` and migration `0005_cap0305_schema_consolidation`). Template exclusion from queues happens via JOIN to `pipelines` + `is_template=false` + `archived_at IS NULL`, NOT via state filtering. Cap #4 introduces zero draft Jobs and does not rely on draft state. (The `draft` state remains in the schema CHECK enum, reserved for cap #10's `gated_on` mechanism.)
- Every Job carries an inline `contract JSONB NOT NULL`; no Contract Profile registry.
- `update_job` is metadata-only; rejects `state`, `labels`, `contract`, and the three claim fields with audit-logged 400s.
- `list_ready_jobs(project_id)` is REQUIRED (not optional); `claim_next_job` mirrors this by requiring `project_id` too.
- Labels are project-scoped; `jobs.labels` TEXT[] cache GIN-indexed; `attach_label`/`detach_label` is the canonical mutation path.
- `get_job`, `get_pipeline`, `get_project` ship with empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays for cap-9 forward-compat. Cap #4's claim response includes a stub Context Packet for cap-8 forward-compat under the same precedent.
- `cancel_job` is the cap-3 state-mutating op; `claim_next_job` (cap-4) joins it as the second cap-3-or-cap-4 path that mutates state without going through `update_job`.

---

## Out of scope (explicit forbids)

Repeated from `capabilities.md` cap #4 scope guardrails plus carry-forward locks:

**From `capabilities.md` cap #4:**
- No `submit_job` — claim works, but the only way to exit `in_progress` here is `release_job`, `reset_claim`, or the auto-release sweep. Submit ships in cap #5.
- No `gated_on` resolution — claim works on any `ready` Job; `draft → ready` promotion via `gated_on` lands in cap #10. Cap-4 only consumes `ready` Jobs.
- No agent-capability registry. Routing is caller-side via `label_filter`. AQ enforces the filter atomically; AQ does not reason about which agent "should" claim what.
- No `parallel_safe` file-conflict flag. Two `ready` Jobs without a `gated_on` edge between them are eligible for concurrent claims by different Actors.

**Carry-forward forbids:**
- No Cycle / Sprint entity (cap-3 lock; not re-litigated).
- No Initiative / Program entity (cap-3 lock).
- No Project Status Update entity (cap-3 lock).
- No Contract Profile registry (cap-3.5 / Decision 3 — the seeded profiles were dropped; AQ2-50 cancelled).
- No new Web views (cap #11 owns UI).
- No webhook subscriptions for agent wake-up (cap-3.5 / Decision 5 — pull-only via MCP polling in v1).

**Cap-4-specific forbids:**
- No `pg_cron` dependency. Sweep is in-process asyncio per Locked Decision 6.
- No multi-tenant / per-project lease overrides (single global `AQ_CLAIM_LEASE_SECONDS`). Per-project override deferred to v1.1.
- No exponential backoff on heartbeat. Constant cadence guidance (~30s) in MCP `set_instructions` text.
- No client-side reconnection logic — caller's responsibility.
- No graceful claim release on agent shutdown — agents that crash trigger sweeper recovery.
- No path-finding / multi-hop dependency analysis (cap-10).

---

## Stories (7, each parented to the cap #4 epic)

Each story carries: Objective, Why this matters (human outcome), Scope (in/out), Verification commands, DoD items table, Depends on, Submission shape.

### Story 4.1 — Schema delta + system actor + config + Pydantic models

**Objective:** One Alembic migration `0006_cap04_indexes_and_system_actor` creates the partial index `idx_jobs_in_progress_heartbeat ON jobs (claim_heartbeat_at, id) WHERE state='in_progress'` and idempotently seeds the reserved `aq-system-sweeper` actor (`kind='script'`). Two new env vars (`AQ_CLAIM_LEASE_SECONDS`, `AQ_CLAIM_SWEEP_INTERVAL_SECONDS`) wired through `_settings.py`. Pydantic request/response models for all four cap-4 ops including the Context Packet stub. Round-trippable: `alembic upgrade head → downgrade -1 → upgrade head` produces identical schema and identical actor-row state.

**Why this matters (human outcome):** The schema and config foundation cap-4 needs exists. The DB knows where stale claims will be looked up (the new partial index). The reserved system actor is in place so the sweep's audit rows have a valid `authenticated_actor_id`. The lease and sweep-interval envs are bounded so a misconfigured deploy can't claim-orphan or over-poll. None of this changes runtime behavior on its own — Story 4.2 layers on the first op that uses it.

**Scope (in):**
- `apps/api/alembic/versions/0006_cap04_indexes_and_system_actor.py` — single revision adding the index and seeding the actor.
- `apps/api/src/aq_api/_settings.py` — add `claim_lease_seconds: int = Field(default=900, validation_alias="AQ_CLAIM_LEASE_SECONDS", ge=60, le=86400)` and `claim_sweep_interval_seconds: int = Field(default=60, validation_alias="AQ_CLAIM_SWEEP_INTERVAL_SECONDS", ge=5, le=3600)`.
- `apps/api/src/aq_api/models/jobs.py` (or a new `claim.py`) — Pydantic models: `ClaimNextJobRequest{project_id: UUID, label_filter: list[LabelName] | None}`, `ClaimNextJobResponse{job: Job, packet: ContextPacketStub, lease_seconds: int, lease_expires_at: datetime, recommended_heartbeat_after_seconds: int}` (per Locked Decision 19), `ContextPacketStub{project_id, pipeline_id, current_job_id, previous_jobs: list[UUID], next_job_id: UUID | None}`, `ReleaseJobResponse{job: Job}`, `ResetClaimRequest{reason: str}` (with `min_length=1` validator on `reason`), `ResetClaimResponse{job: Job}`, `HeartbeatJobResponse{job: Job}`. All `extra='forbid', frozen=True` per cap-1 lock. UTC datetimes via `aq_api._datetime` (cap-1 carry-forward).
- Re-export from `apps/api/src/aq_api/models/__init__.py`.

**Scope (out):**
- No service layer (Story 4.2+).
- No routes, no CLI, no MCP wiring.
- No SQLAlchemy ORM relationship changes.

**Verification:**
```
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
docker compose exec -T db psql -U aq -d aq2 -c "\d jobs" | grep idx_jobs_in_progress_heartbeat   # index present
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM actors WHERE name='aq-system-sweeper' AND kind='script' AND deactivated_at IS NULL"   # = 1
docker compose exec -T api uv run alembic -c apps/api/alembic.ini downgrade -1
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head    # idempotent
docker compose exec -T api uv run pytest -q apps/api/tests/test_models_cap04.py
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/models/ apps/api/src/aq_api/_settings.py
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.1-01 | `idx_jobs_in_progress_heartbeat` partial btree index present on `jobs` | command | `artifacts/cap-04/schema-delta.txt` | `\d jobs` shows the index with predicate `state='in_progress'` |
| DOD-AQ2-S4.1-02 | `aq-system-sweeper` actor seeded idempotently across four cases: (a) missing → inserts one active row; (b) active row exists → no-op; (c) deactivated row exists → inserts new active row alongside; (d) two concurrent migrations / startup paths race → exactly one active row remains | test + command | `artifacts/cap-04/system-actor-seed-cases.xml` + `system-actor-count.txt` | pytest covers all four cases against a clean test DB; final state per case asserted via SQL count |
| DOD-AQ2-S4.1-03 | Migration round-trips (upgrade → downgrade -1 → upgrade) cleanly | command | `artifacts/cap-04/alembic-roundtrip.txt` | both upgrade runs succeed; second upgrade produces no schema change |
| DOD-AQ2-S4.1-04 | `AQ_CLAIM_LEASE_SECONDS` validates `[60, 86400]`; out-of-range values fail boot with `ValidationError` | test | `artifacts/cap-04/settings-validation.xml` | pytest covers `60`, `86400`, `59`, `86401`, missing-env defaults, expects exact range errors |
| DOD-AQ2-S4.1-05 | `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` validates `[5, 3600]`; same rigor | test | same file | `5`, `3600`, `4`, `3601`, missing-env defaults |
| DOD-AQ2-S4.1-06 | All cap-4 Pydantic models have `extra='forbid', frozen=True`; Packet stub fields match Locked Decision 13 exactly | grep + test | `artifacts/cap-04/models-shape.txt` | grep count of `extra='forbid'` matches expected; pytest round-trips models |
| DOD-AQ2-S4.1-07 | mypy strict passes on new models + settings | command | `artifacts/cap-04/mypy-cap04-models.txt` | clean |

**Depends on:** Cap #3.5 schema (jobs has `claim_heartbeat_at`, pipelines has `is_template` + `archived_at`).

**Submission shape (ADR-AQ-030):** `outcome ∈ {done, failed, blocked}`. `dod_results[]` = 7 entries. `files_changed[]` = the migration + `_settings.py` + new model files + `__init__.py` re-exports. `risks_or_deviations` = `[]` unless something hit. `handoff = "AQ2-S4.2 (Story 4.2 — claim_next_job atomic claim service + REST + CLI + MCP, C1 checkpoint)"`. First commit on branch `aq2-cap-04`.

---

### Story 4.2 — `claim_next_job` (atomic claim service + REST + CLI + MCP) — CHECKPOINT C1

**Objective:** Ship the canonical `claim_next_job` op across all three surfaces. Service in `apps/api/src/aq_api/services/claim.py`; route `POST /jobs/claim`; CLI `aq job claim --project <uuid> [--label area:web]`; MCP tool `claim_next_job(project_id, label_filter)` with `destructiveHint=true`. Atomic single-transaction semantics: explicit `async with session.begin()` wraps the SELECT-FOR-UPDATE-SKIP-LOCKED pick, the UPDATE that sets `state='in_progress'` + `claimed_by_actor_id` + `claimed_at` + `claim_heartbeat_at`, and the audit-row insert. JOIN to pipelines per Locked Decision 9 to exclude template + archived Pipelines. FIFO ordering via `ORDER BY created_at ASC, id ASC`. Multi-part MCP response: Job + Packet stub + text block. Parity tests regenerated for `claim_next_job`. C1 checkpoint fires here.

**Why this matters (human outcome):** This is the cap. After Story 4.2 lands, an agent can call `aq job claim --project <id>` and atomically receive one `ready` Job. Two agents racing produce one winner; the loser sees a clean `409 no_ready_job` (no Job to claim, not because they lost the race specifically but because the SKIP LOCKED contract makes the loser see "no remaining row I can lock"). The audit log captures every claim attempt with its resolved `label_filter`, answering the forensic question "why did this agent get this Job?" later. C1 is the natural mid-capability stop because the loop is already exercisable — claim → (wait for cap-5 submit, but for now use update_job to verify state is `in_progress`).

**Scope (in):**
- `apps/api/src/aq_api/services/claim.py` (new) — `claim_next_job(session, *, request, actor_id) -> ClaimNextJobResponse`. Uses `audited_op` for the same-tx audit guarantee. Module-level constant `RECOMMENDED_HEARTBEAT_AFTER_SECONDS = 30` per Locked Decision 19. Builds the SELECT FOR UPDATE SKIP LOCKED query mirroring `list_ready_jobs.py:29-77`. On no-row, raises `BusinessRuleException(409, 'no_ready_job', ...)` — `audited_op` writes the denial audit row with `target_id=NULL` per Locked Decision 12. On success, executes the UPDATE, builds the Packet stub from the Job's `pipeline_id`/`project_id`, computes `lease_expires_at = job.claimed_at + timedelta(seconds=settings.claim_lease_seconds)` (UTC, Z-form), constructs `ClaimNextJobResponse(job, packet, lease_seconds=settings.claim_lease_seconds, lease_expires_at=lease_expires_at, recommended_heartbeat_after_seconds=RECOMMENDED_HEARTBEAT_AFTER_SECONDS)`, sets `audit.target_id = job.id`, sets `audit.response_payload = response.model_dump(...)`.
- `apps/api/src/aq_api/routes/jobs.py` (modify) — add `POST /jobs/claim` route with Pydantic `ClaimNextJobRequest` body and `ClaimNextJobResponse` response.
- `apps/cli/src/aq_cli/main.py` (modify) — add `aq job claim --project <uuid> [--label LABEL]...` Typer command (`--label` is repeatable). Shells to REST.
- `apps/api/src/aq_api/mcp.py` (modify) — register `claim_next_job` tool with `annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}`. Multi-part output: return a list of FastMCP `Content` blocks (Job JSON + Packet JSON + text block per Locked Decision 18).
- `apps/api/tests/test_claim_next_job.py` (new) — happy path (single Job), label filter, no-ready-job 409, cross-project isolation, template-Pipeline-Job-not-claimed, archived-Pipeline-Job-not-claimed, audit row shape (success + denial).
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated to include `claim_next_job` schema only (other cap-4 ops not yet shipped).
- `tests/parity/test_four_surface_parity.py` — add `-k claim` parametrized case asserting REST + CLI + MCP byte-equal payloads (Web skipped per cap-3 precedent).
- EXPLAIN evidence: `artifacts/cap-04/explain-claim-no-label.txt` and `artifacts/cap-04/explain-claim-with-labels.txt`.

**Scope (out):**
- No release / reset / heartbeat (Stories 4.3, 4.4).
- No sweep (Story 4.5).
- No `set_instructions` server-level block (Story 4.6).
- No race / atomicity tests (Story 4.6).
- No web view (cap #11).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_claim_next_job.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k claim
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/claim.py
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T db psql -U aq -d aq2 -c "EXPLAIN (ANALYZE, BUFFERS) SELECT j.id FROM jobs j JOIN pipelines p ON j.pipeline_id = p.id WHERE j.state='ready' AND j.project_id='<seeded-project-id>' AND p.is_template=false AND p.archived_at IS NULL ORDER BY j.created_at, j.id LIMIT 1 FOR UPDATE SKIP LOCKED"
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.2-01 | `claim_next_job` succeeds on a `ready` Job: state→`in_progress`, `claimed_by_actor_id`/`claimed_at`/`claim_heartbeat_at` all set; success audit row written | test | `artifacts/cap-04/claim-success.xml` | live test asserts all five fields + audit row |
| DOD-AQ2-S4.2-02 | Label filter restricts to matching Jobs in FIFO order | test | same | 5 Jobs with mixed labels; claim with `area:api` returns oldest area:api Job, skipping older area:web Jobs |
| DOD-AQ2-S4.2-03 | Template-Pipeline Jobs and archived-Pipeline Jobs are excluded by the JOIN predicate, NOT by state filtering | test | same | live test asserts: (a) the cap-3.5 seeded `ship-a-thing` template Pipeline has `state='ready'` Jobs (verified pre-claim via DB query), and (b) `claim_next_job` does NOT return them because of `p.is_template=false`. Repeat for a non-template Pipeline with `archived_at IS NOT NULL`. |
| DOD-AQ2-S4.2-04 | No `ready` Job → 409 `no_ready_job` with audit row (target_id=NULL, request_payload contains project_id + label_filter) | test | `artifacts/cap-04/claim-no-ready-job.xml` | live test asserts response shape + audit row contents |
| DOD-AQ2-S4.2-05 | Invalid Pydantic input (missing project_id, malformed UUID) → 422, NOT audited (request never reached service) | test | same | `audit_log` count delta = 0 across 5 invalid requests |
| DOD-AQ2-S4.2-06 | Claim audit row records resolved `label_filter` in `request_payload` | test | `artifacts/cap-04/claim-audit-shape.xml` | live test asserts `audit_log.request_payload->>'label_filter'` matches input |
| DOD-AQ2-S4.2-07 | Claim query uses `idx_jobs_state_project_created` (no label) and combines with `idx_jobs_labels_gin` (with label filter) | command | `artifacts/cap-04/explain-claim-no-label.txt`, `explain-claim-with-labels.txt` | EXPLAIN plans show expected index access |
| DOD-AQ2-S4.2-08 | MCP `claim_next_job` returns multi-part content list (Job + Packet stub + text block) | test | `artifacts/cap-04/mcp-claim-multipart.xml` | live MCP client call asserts 3-block response with correct types |
| DOD-AQ2-S4.2-09 | Parity test green for `claim_next_job` across REST + CLI + MCP | test | `artifacts/cap-04/parity-claim.xml` | byte-equal payloads |
| DOD-AQ2-S4.2-10 | Packet stub shape exactly matches Locked Decision 13 (no Contract Profile fields, empty arrays / null pointers) | test | same | response schema compared against locked shape |
| DOD-AQ2-S4.2-11 | Claim response includes `lease_seconds`, `lease_expires_at`, `recommended_heartbeat_after_seconds` per Locked Decision 19; values match settings + 30s constant; `lease_expires_at = claimed_at + lease_seconds` exactly | test | `artifacts/cap-04/claim-lease-fields.xml` | live test with `AQ_CLAIM_LEASE_SECONDS=120` asserts response carries 120 + 30 + computed timestamp |

**Depends on:** Story 4.1.

**Submission shape (ADR-AQ-030):** Second commit. `dod_results[]` = 10 entries. `handoff = "AQ2-S4.3 (Story 4.3 — release_job + reset_claim)"`. **CHECKPOINT C1 fires here** — Codex stops, posts evidence on cap-4 epic, awaits Ghost approval before Story 4.3.

---

### Story 4.3 — `release_job` + `reset_claim`

**Objective:** Two ops sharing a service module (`apps/api/src/aq_api/services/release.py`). `release_job` is claimant-only, transitions `in_progress` → `ready`, NULLs all three claim fields. `reset_claim` is any-actor (per cap-2 "no authorization tiers"), requires non-empty `reason: str`, transitions `in_progress` → `ready`, NULLs all three claim fields, records reason in audit `request_payload`. Routes, CLI commands, MCP tools added incrementally with snapshot regeneration.

**Why this matters (human outcome):** A claimant who realizes the work isn't theirs to do can call `aq job release` and return it cleanly. A human or any agent recovering from a known-dead claim can call `aq job reset-claim --reason "claimant crashed"` to free the work — explicit human escape hatch even before the auto-release sweep ships in Story 4.5. Together with claim, this exercises the full claim → release loop end-to-end.

**Scope (in):**
- `apps/api/src/aq_api/services/release.py` (new) — `release_job(session, *, job_id, actor_id) -> ReleaseJobResponse` and `reset_claim(session, *, job_id, request, actor_id) -> ResetClaimResponse`. Both use `audited_op` per the standard pattern. Both write the per-claim-field-clearing UPDATE per Locked Decision 10. `release_job` checks `claimed_by_actor_id == actor_id` (raise `BusinessRuleException(403, 'release_forbidden', ...)` on mismatch). Both check `state='in_progress'` (raise `BusinessRuleException(409, 'job_not_claimed', ...)` if not). Both 404 on unknown Job.
- `apps/api/src/aq_api/routes/jobs.py` (modify) — add `POST /jobs/{id}/release` and `POST /jobs/{id}/reset-claim` (body `{reason: str}`).
- `apps/cli/src/aq_cli/main.py` (modify) — add `aq job release <job-id>` and `aq job reset-claim <job-id> --reason "..."`. Reason is required.
- `apps/api/src/aq_api/mcp.py` (modify) — register `release_job` and `reset_claim` tools with `destructiveHint=true`.
- `apps/api/tests/test_release_job.py` (new) — claimant happy path, wrong-claimant 403, not-claimed 409, not-found 404, audit row shapes.
- `apps/api/tests/test_reset_claim.py` (new) — any-actor happy path, reason recorded in audit, missing-reason 422, not-claimed 409, not-found 404.
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated to add `release_job` + `reset_claim` schemas (alongside cap-4 Story 4.2's `claim_next_job`).
- `tests/parity/test_four_surface_parity.py` — add `-k release`, `-k reset_claim` parametrized cases.

**Scope (out):**
- No heartbeat (Story 4.4).
- No sweep (Story 4.5).
- No race tests (Story 4.6).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_release_job.py apps/api/tests/test_reset_claim.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k "release or reset_claim"
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/release.py
docker compose exec -T api uv run ruff check apps/api apps/cli
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.3-01 | `release_job` by claimant: state→`ready`, all three claim fields NULL, success audit row | test | `artifacts/cap-04/release-success.xml` | live test asserts all four conditions |
| DOD-AQ2-S4.3-02 | `release_job` by claimant from any concurrent state ≠ `in_progress` returns 409 (e.g., another actor reset between claim and release) | test | same | live test sets state to `ready` mid-flow; release returns 409 |
| DOD-AQ2-S4.3-03 | `release_job` denial audit shape: target_id=job_id, error_code, request_payload | test | `artifacts/cap-04/release-denials.xml` | live test asserts shape for each denial |
| DOD-AQ2-S4.3-04 | `release_job` wrong claimant → 403 `release_forbidden` with audit row | test | same | live test |
| DOD-AQ2-S4.3-05 | `release_job` not claimed / not `in_progress` → 409 `job_not_claimed` with audit row | test | same | live test |
| DOD-AQ2-S4.3-06 | `release_job` not found → 404 `job_not_found` with audit row | test | same | live test |
| DOD-AQ2-S4.3-07 | `reset_claim` any actor: state→`ready`, all three claim fields NULL, success audit row contains reason in `request_payload.reason` | test | `artifacts/cap-04/reset-claim-success.xml` | live test asserts all conditions |
| DOD-AQ2-S4.3-08 | `reset_claim` not claimed → 409 `job_not_claimed` with audit row | test | `artifacts/cap-04/reset-claim-denials.xml` | live test |
| DOD-AQ2-S4.3-09 | `reset_claim` empty/missing `reason` → 422 (Pydantic), NOT audited | test | same | live test asserts audit_log count delta = 0 |
| DOD-AQ2-S4.3-10 | `reset_claim` not found → 404 `job_not_found` with audit row | test | same | live test |
| DOD-AQ2-S4.3-11 | Parity test green for `release_job` and `reset_claim` across REST + CLI + MCP | test | `artifacts/cap-04/parity-release-reset.xml` | byte-equal payloads |

**Depends on:** Story 4.2 (claim must exist to be released or reset).

**Submission shape (ADR-AQ-030):** Third commit. `dod_results[]` = 11 entries. `handoff = "AQ2-S4.4 (Story 4.4 — heartbeat_job + audited_op skip-success refactor)"`.

---

### Story 4.4 — `heartbeat_job` + `audited_op` `skip_success_audit` refactor

**Objective:** Refactor `apps/api/src/aq_api/_audit.py:audited_op` to accept `skip_success_audit: bool = False` per Locked Decision 4 (four-path semantics). Implement `heartbeat_job` service in `apps/api/src/aq_api/services/heartbeat.py` using `audited_op(skip_success_audit=True)`. Claimant-only, `state='in_progress'`-only, updates `claim_heartbeat_at = now()` and only that field. Cross-claimant attempt → 403 `heartbeat_forbidden` (audited per the denial path, which always audits regardless of the skip-success flag). Non-`in_progress` Job → 409 `job_not_in_progress` (audited). Routes, CLI, MCP added incrementally.

**Why this matters (human outcome):** An agent claims a Job, starts working, and calls `aq job heartbeat <job-id>` every ~30 seconds (recommendation; not enforced). Each successful heartbeat refreshes `claim_heartbeat_at` to `now()` so the auto-release sweep (Story 4.5) doesn't reclaim the work prematurely. Successful heartbeats do NOT clutter the audit log with ~3 rows per minute of business-trivia (the Job's `claim_heartbeat_at` column already stores the only state AQ needs). Denials (wrong claimant, terminal Job) DO audit, preserving the forensic trail for ownership disputes and stale-claim escapes.

**Scope (in):**
- `apps/api/src/aq_api/_audit.py` (modify) — add `skip_success_audit: bool = False` keyword-only parameter to `audited_op`. Implement the four-path semantics from Locked Decision 4. Existing call sites (cap-1/2/3) carry the default and behave identically.
- `apps/api/src/aq_api/services/heartbeat.py` (new) — `heartbeat_job(session, *, job_id, actor_id) -> HeartbeatJobResponse`. Enters `audited_op` with `skip_success_audit=True`. Inside: SELECT job FOR UPDATE; check existence (raise 404), claimant (raise 403), state (raise 409); UPDATE `claim_heartbeat_at = now()`; flush. The `audited_op` denial path audits 403/409/404; the success path commits without an audit row.
- `apps/api/src/aq_api/routes/jobs.py` (modify) — add `POST /jobs/{id}/heartbeat`.
- `apps/cli/src/aq_cli/main.py` (modify) — add `aq job heartbeat <job-id>`.
- `apps/api/src/aq_api/mcp.py` (modify) — register `heartbeat_job` tool with `destructiveHint=true`.
- `apps/api/tests/test_heartbeat_job.py` (new) — claimant happy path (no audit row written), heartbeat advances `claim_heartbeat_at`, wrong-claimant 403 (audit row written), non-`in_progress` 409 (audit row written), not-found 404 (audit row written), success-not-audited assertion (audit_log count delta = 0 after N heartbeats).
- `apps/api/tests/test_audited_op.py` (modify or new) — direct unit test of the four-path semantics: success/skip-success/denial/exception. Asserts every existing cap-1/2/3 mutation route still has identical audit behavior (carry-forward regression test).
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated to add `heartbeat_job` schema.
- `tests/parity/test_four_surface_parity.py` — add `-k heartbeat`.

**Scope (out):**
- No sweep (Story 4.5).
- No race / atomicity tests (Story 4.6).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_heartbeat_job.py apps/api/tests/test_audited_op.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k heartbeat
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/_audit.py apps/api/src/aq_api/services/heartbeat.py
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests   # full regression
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.4-01 | Successful `heartbeat_job` advances `claim_heartbeat_at` and writes ZERO audit rows | test | `artifacts/cap-04/heartbeat-success-no-audit.xml` | live test: 10 heartbeats → `audit_log` count delta = 0; `claim_heartbeat_at` advances each call |
| DOD-AQ2-S4.4-02 | `audited_op(skip_success_audit=True)` four-path semantics behave per Locked Decision 4 | test | `artifacts/cap-04/audited-op-four-paths.xml` | direct unit test covers all four paths; assertion on commit/rollback behavior matches table |
| DOD-AQ2-S4.4-03 | All cap-1/2/3 mutation routes still audit successes (regression check on the refactor) | test | `artifacts/cap-04/audit-regression.xml` | run cap-3 test suite; all existing audit assertions still pass |
| DOD-AQ2-S4.4-04 | Cross-claimant heartbeat → 403 `heartbeat_forbidden` with audit row | test | `artifacts/cap-04/heartbeat-denials.xml` | live test |
| DOD-AQ2-S4.4-05 | Heartbeat on non-`in_progress` Job → 409 `job_not_in_progress` with audit row | test | same | live test (covers `ready`, `done`-via-cap-3-cancel-job-hack, `cancelled`) |
| DOD-AQ2-S4.4-06 | Heartbeat on missing Job → 404 `job_not_found` with audit row | test | same | live test |
| DOD-AQ2-S4.4-07 | Parity test green for `heartbeat_job` across REST + CLI + MCP | test | `artifacts/cap-04/parity-heartbeat.xml` | byte-equal payloads |

**Depends on:** Story 4.2 (claim must exist to be heartbeated).

**Submission shape (ADR-AQ-030):** Fourth commit. `dod_results[]` = 7 entries. `handoff = "AQ2-S4.5 (Story 4.5 — auto-release sweep + lifespan integration)"`.

---

### Story 4.5 — Auto-release sweep + lifespan integration

**Objective:** Implement the in-process asyncio sweep that auto-releases stuck `in_progress` Jobs whose `claim_heartbeat_at` is older than `AQ_CLAIM_LEASE_SECONDS`. The authoritative test surface is `run_claim_auto_release_once(session, *, now, system_actor_id=None) -> int` in `apps/api/src/aq_api/services/claim_auto_release.py`. The function sets the `authenticated_actor_id` contextvar internally (via `set_authenticated_actor_id(system_actor_id)`) with `try/finally` reset, ensuring direct tests don't depend on a request context. The background loop in `apps/api/src/aq_api/app.py`'s lifespan opens a fresh session per iteration and calls the function. `ensure_system_actor(session)` runs once at startup (safety valve over the migration seed). Lifespan wraps `mcp_http_app.lifespan` cleanly — task created post-yield-startup, cancelled on shutdown with `try/except CancelledError`.

**Why this matters (human outcome):** A crashed agent's claim doesn't sit forever. After 15 minutes (the default lease) without a heartbeat, the sweep flips the Job back to `ready` and writes an audit row attributed to the reserved `aq-system-sweeper` actor. Operators who need a faster recovery still have `reset_claim` (the explicit human escape hatch). The deterministic test surface (`run_claim_auto_release_once(now=...)`) means CI can prove the sweep works without real-time `asyncio.sleep` calls.

**Scope (in):**
- `apps/api/src/aq_api/services/claim_auto_release.py` (new):
  - `ensure_system_actor(session) -> UUID` — idempotent SELECT (active actor by name) → INSERT-if-missing. Catches `IntegrityError` from racing `INSERT`, rolls back, re-`SELECT`s, and returns the winner's UUID. **Decision: if a deactivated `aq-system-sweeper` actor exists, this function inserts a NEW active row** (does not reactivate; reactivation would conflate operator-driven deactivation with service-forced re-activation). The partial-unique index `actors_name_active_uniq (name) WHERE deactivated_at IS NULL` permits multiple inactive rows + one active row, so this is well-formed.
  - `run_claim_auto_release_once(session, *, now: datetime, system_actor_id: UUID | None = None) -> int` — single sweep iteration. Resolves system actor via `ensure_system_actor` if not passed. Sets `authenticated_actor_id` contextvar inside try/finally so direct tests don't depend on a request context. Queries `SELECT id, claimed_by_actor_id, claim_heartbeat_at FROM jobs WHERE state='in_progress' AND claim_heartbeat_at < :stale_before ORDER BY claim_heartbeat_at, id LIMIT 100 FOR UPDATE SKIP LOCKED` (where `stale_before = now - lease`) — uses `idx_jobs_in_progress_heartbeat`. **Locked batch size: 100 per iteration** (keeps any single transaction bounded; backlogs > 100 drain across sweep cycles). Returns the count of released Jobs.
  - **Auto-release audit path locked single-path** (per Codex P1-2 + Mario lock 2):
    - `claim_auto_release` is a **successful system mutation**. Not a denial. Not a `BusinessRuleException` path.
    - Implementation extends `AuditOperation` with `error_code: str | None = None`. The `audited_op` success path passes `audit.error_code` through to `record(...)` when present. Existing call sites default to `None` and produce identical audit shapes.
    - `run_claim_auto_release_once` for each stale Job: enters `audited_op(op='claim_auto_release', target_kind='job', target_id=job.id, request_payload={previous_claimant_actor_id, stale_claim_heartbeat_at, lease_seconds, reason: 'lease_expired'})`, executes the UPDATE that sets `state='ready'` and NULLs the three claim fields, sets `audit.error_code = 'lease_expired'` and `audit.response_payload = {released: True, previous_claimant_actor_id, ...}`, then exits the context manager normally — the success path writes the audit row with `error_code='lease_expired'`.
    - **Do NOT model auto-release as `BusinessRuleException`.** **Do NOT use the denial path.** Both would roll back the state mutation per the existing `audited_op` semantics, breaking same-transaction release-with-audit.
  - `claim_auto_release_loop(initial_system_actor_id: UUID | None)` — coroutine that calls `run_claim_auto_release_once` on `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` cadence. Catches `asyncio.CancelledError` cleanly. **If `initial_system_actor_id` is None** (because startup's `ensure_system_actor` failed transiently), the loop calls `ensure_system_actor` at the start of each iteration; once successful, it caches the UUID for subsequent iterations. Logs a warning on each retry. Does NOT take down request handling — request mutations continue to use their per-request authenticated actor (the system actor is sweep-only).
- `apps/api/src/aq_api/_audit.py` (modify, second time after Story 4.4) — extend `AuditOperation` dataclass with `error_code: str | None = None`. `audited_op` success path passes `audit.error_code` to `record(...)` when set; default `None` preserves current behavior. Document the extension as cap-4-introduced (auto-release is the only success-with-error_code call site in cap-4).
- `apps/api/src/aq_api/app.py` (modify) — replace `lifespan=mcp_http_app.lifespan` with a wrapping `@asynccontextmanager`. **Startup is robust to transient ensure_system_actor failure** (Mario lock 5):
  ```
  @asynccontextmanager
  async def app_lifespan(app: FastAPI):
      async with mcp_http_app.lifespan(app):
          # Best-effort: resolve system actor at startup. If the DB is
          # transiently unavailable or a race fires, log and proceed.
          # The sweep loop will retry on its next interval.
          system_actor_id: UUID | None = None
          try:
              async with SessionLocal() as session:
                  system_actor_id = await ensure_system_actor(session)
                  await session.commit()
          except Exception as exc:
              logger.warning(
                  "ensure_system_actor failed at startup; sweep loop will retry: %s",
                  exc,
              )
          sweep_task = asyncio.create_task(
              claim_auto_release_loop(system_actor_id)
          )
          try:
              yield
          finally:
              sweep_task.cancel()
              try:
                  await sweep_task
              except asyncio.CancelledError:
                  pass
  ```
  **API request handling is NOT taken down by transient sweep setup failure.** The sweep loop owns its own ensure-and-retry path.
- `apps/api/tests/test_claim_auto_release_sweep.py` (new) — deterministic tests using mocked `now`:
  - Single stale Job → sweep releases it; audit row written with `op='claim_auto_release'`, `target_id=job_id`, `authenticated_actor_id=<system actor>`, `error_code='lease_expired'`, payload contains previous claimant.
  - Fresh Job (within lease) → sweep does NOT touch it.
  - Multiple stale Jobs in one batch → all released; one audit row each; per-Job atomicity (Story 4.6 will inject failures).
  - System actor missing at sweep time (e.g., test deletes it) → `ensure_system_actor` recreates it; sweep proceeds.
  - Test does NOT use real `asyncio.sleep`; calls `run_claim_auto_release_once(session, now=mocked_future)` directly.
- `apps/api/tests/test_lifespan_sweep.py` (new) — integration test that boots the app, asserts the sweep task is created on startup and cancelled on shutdown without leaking the contextvar.

**Scope (out):**
- No race / atomicity injection tests (Story 4.6).
- No MCP `set_instructions` block (Story 4.6).
- No `capabilities.md` fix-up (Story 4.7).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_claim_auto_release_sweep.py apps/api/tests/test_lifespan_sweep.py
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/claim_auto_release.py apps/api/src/aq_api/app.py
docker compose exec -T api uv run ruff check apps/api
docker compose exec -T db psql -U aq -d aq2 -c "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM jobs WHERE state='in_progress' AND claim_heartbeat_at < now() - interval '900 seconds' FOR UPDATE SKIP LOCKED LIMIT 100"
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.5-01 | `run_claim_auto_release_once(now=...)` releases stale Jobs deterministically (no real sleep) | test | `artifacts/cap-04/sweep-deterministic.xml` | mocked `now`, asserts state transitions and audit rows |
| DOD-AQ2-S4.5-02 | Sweep does NOT touch fresh Jobs (within lease) | test | same | test case for `claim_heartbeat_at = now() - (lease/2)` |
| DOD-AQ2-S4.5-03 | Sweep query uses `idx_jobs_in_progress_heartbeat` | command | `artifacts/cap-04/explain-sweeper-stale-claim.txt` | EXPLAIN plan shows `Index Scan using idx_jobs_in_progress_heartbeat` |
| DOD-AQ2-S4.5-04 | `claim_auto_release` audit row shape exactly per Locked Decisions 11 + 21: `target_kind='job'`, `target_id=job_id`, `authenticated_actor_id=<system actor>`, `error_code='lease_expired'`, payload contains `previous_claimant_actor_id` + `stale_claim_heartbeat_at` + `lease_seconds`. Audit row written via the **success path** of `audited_op` (using the new `AuditOperation.error_code` field), NOT via `BusinessRuleException` / denial path | test | `artifacts/cap-04/sweep-audit-shape.xml` | live test asserts every field; pytest verifies audit row exists AND state transition to `ready` happened in the same transaction |
| DOD-AQ2-S4.5-05 | `ensure_system_actor` is idempotent: existing actor → returns same UUID; missing actor → inserts and returns new UUID | test | `artifacts/cap-04/system-actor-idempotent.xml` | live test runs the function 3× back-to-back; count remains 1 |
| DOD-AQ2-S4.5-06 | Sweep released-Job clears all three claim fields (per Locked Decision 10) | test | `artifacts/cap-04/sweep-field-clearing.xml` | live test asserts NULLs |
| DOD-AQ2-S4.5-07 | App lifespan creates and cancels sweep task cleanly; no contextvar leakage post-shutdown | test | `artifacts/cap-04/lifespan-sweep.xml` | live test boots+shuts the app, asserts task lifecycle and contextvar reset |
| DOD-AQ2-S4.5-08 | Sweep handles missing system actor gracefully (logs + recreates via `ensure_system_actor`; does NOT crash the loop) | test | same | test deletes the actor mid-loop; asserts sweep recovers |
| DOD-AQ2-S4.5-09 | API startup tolerates transient `ensure_system_actor` failure (per Locked Decision 20): lifespan logs warning, proceeds with `system_actor_id=None`, sweep loop retries per iteration until success | test | `artifacts/cap-04/lifespan-startup-retry.xml` | live test simulates DB-unavailable at boot; asserts API serves request handlers immediately; sweep loop logs+retries; eventually succeeds and resumes auto-release |
| DOD-AQ2-S4.5-10 | `ensure_system_actor` race-safety: two concurrent calls produce exactly one active actor row; `IntegrityError` from racing INSERTs is caught, rolled back, and re-SELECT'd | test | `artifacts/cap-04/ensure-actor-race.xml` | live test: two asyncio tasks call `ensure_system_actor` simultaneously; both return same UUID; final count = 1 |

**Depends on:** Stories 4.1, 4.2, 4.3, 4.4.

**Submission shape (ADR-AQ-030):** Fifth commit. `dod_results[]` = 8 entries. `risks_or_deviations` = `["AuditOperation extended with error_code field for sweep success-with-error-code semantics — see Story 4.5 scope"]`. `handoff = "AQ2-S4.6 (Story 4.6 — MCP richness + race + atomicity)"`.

---

### Story 4.6 — MCP richness (`set_instructions` + multi-part response refinement) + race + atomicity

**Objective:** Ship the MCP richness pattern locked from cap #4 forward (Locked Decision 18). Add `mcp.set_instructions(...)` server-level block to `mcp.py` with the agent_identity + error-shape + heartbeat-cadence + "next call" guidance text. Refine `claim_next_job`'s multi-part content list to match the spec exactly (Job + Packet + text). Ship the race test (`tests/parity/test_claim_race.py` — 50× concurrent claimers) and the per-Job atomicity tests (`tests/atomicity/test_claim_atomicity.py` for the claim path; `tests/atomicity/test_claim_auto_release_atomicity.py` for the sweep). Create the `tests/atomicity/` directory if it doesn't exist.

**Why this matters (human outcome):** Cap-4 unblocks cap-5 (submit), cap-6 (dogfood), and the entire downstream agent-claims-and-works flow. The riskiest assumption from the brief — "SKIP-LOCKED + same-tx audit holds at 50× concurrency without producing duplicate winners or missed audit rows" — gets validated mechanically here. The MCP richness pattern (instructions + multi-part output) sets the contract every cap from #4 forward inherits.

**Scope (in):**
- **FastMCP multi-part output preflight spike (FIRST commit of Story 4.6)** per Locked Decision 22. Write a throwaway test (`apps/api/tests/test_fastmcp_multipart_spike.py`) that registers a temporary FastMCP tool returning a `list[Content]` (or whatever the installed FastMCP version's exact return type is — read `pyproject.toml` for the pinned FastMCP version, then check the installed package's type signatures). Run the live MCP HTTP transport against it and assert the multi-part response shape arrives at a real MCP client. Capture the exact return-type signature in `artifacts/cap-04/fastmcp-multipart-spike.txt`. **If the installed FastMCP version's API differs from the assumption (`list[Content]` blocks with type+data fields)**, amend Story 4.6 with a one-line clarification of the actual return type BEFORE wiring `claim_next_job`'s multi-part response. The spike test is deleted in the same Story 4.6 commit cycle once the real `claim_next_job` multi-part wiring is shipped (no need to keep the throwaway test in the suite).
- `apps/api/src/aq_api/mcp.py` (modify) — add `mcp.set_instructions(...)` call before tool registrations. Instructions text:
  ```
  You are connected to AgenticQueue 2.0's MCP server.

  Conventions:
  - Pass `agent_identity` (your API key alias) on every call. AQ does not infer it.
  - Errors come back as structured objects: {error_code, rule_violated, details}.
    On `rule_violated`, do NOT retry — it indicates a fixable client mistake
    (wrong claimant, wrong state, missing field), not a transient failure.
  - After a successful `claim_next_job`: the response includes a Context Packet
    (cap #8 forward-compat — currently a stub with empty `previous_jobs[]` and
    `next_job_id: null`). Read the Job's inline `contract` field for the DoD,
    call `heartbeat_job` every ~30 seconds while working, and call `submit_job`
    (cap #5 — not yet shipped) when done. For now, use `release_job` to return
    the Job to `ready` if you cannot complete it.
  - Heartbeat cadence is recommended ~30 seconds. The server enforces only the
    AQ_CLAIM_LEASE_SECONDS lease (default 900s = 15 minutes); shorter cadence
    is friendlier to the auto-release sweep.
  ```
- `apps/api/src/aq_api/mcp.py` (modify) — refine `claim_next_job` tool to return a `list[Content]` with three blocks:
  1. JSON content block with the Job (Pydantic dump).
  2. JSON content block with the Packet stub (Pydantic dump).
  3. Text content block: `f"You claimed Job {job.id} ({job.title}). Read the inline contract for the DoD; heartbeat every ~30s; submit_job ships in cap #5."`
- `tests/atomicity/__init__.py` + `tests/atomicity/test_claim_atomicity.py` (new) — monkeypatch `session.flush()` to fail mid-claim (after the SELECT FOR UPDATE finds a Job, before the UPDATE commits); assert the whole transaction rolls back: no `state='in_progress'`, no audit row.
- `tests/atomicity/test_claim_auto_release_atomicity.py` (new) — same monkeypatch pattern for the sweep; inject a failure on Job N of a 5-Job batch; assert Jobs 1..N-1 are released-with-audit, Job N is rolled back (still `in_progress`, no audit row), Jobs N+1..5 are untouched per the per-Job atomicity invariant (Locked Decision 8).
- `tests/parity/test_claim_race.py` (new) — 50 concurrent HTTP clients (each with a distinct API key), all calling `POST /jobs/claim` against a small pool of Jobs. Asserts:
  - Exactly N winners (where N = pool size); rest get `409 no_ready_job`.
  - One audit row per winner (`op='claim_next_job'`, success); one audit row per loser (`op='claim_next_job'`, denial, `error_code='no_ready_job'`).
  - No Job has two `claimed_by_actor_id` (DB query `SELECT count(*) FROM jobs WHERE claimed_by_actor_id IS NOT NULL` matches N).
  - Total audit row count = 50 (one per request, success or denial).
- `apps/api/tests/test_mcp_richness.py` (new or modify) — assert MCP `tools/list` returns `claim_next_job` / `release_job` / `reset_claim` / `heartbeat_job` with `destructiveHint=true`, `readOnlyHint=false`, `idempotentHint=false`. Assert MCP `initialize` response carries the server `instructions` text. Assert `claim_next_job` MCP call returns a 3-block content list.

**Scope (out):**
- No new ops.
- No `capabilities.md` fix-up (Story 4.7).
- No final evidence pack (Story 4.7).

**Verification:**
```
docker compose exec -T api uv run pytest -q tests/atomicity/
docker compose exec -T api uv run pytest -q tests/parity/test_claim_race.py
docker compose exec -T api uv run pytest -q apps/api/tests/test_mcp_richness.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py    # full parity rerun
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/mcp.py
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.6-01 | MCP `initialize` response contains the server `instructions` text per Locked Decision 18 | test | `artifacts/cap-04/mcp-instructions.xml` | live MCP client asserts the text matches |
| DOD-AQ2-S4.6-02 | All 4 cap-4 mutation tools have `{destructiveHint: true, readOnlyHint: false, idempotentHint: false}` in MCP `tools/list` | test | `artifacts/cap-04/mcp-annotations.xml` | live MCP client asserts annotations |
| DOD-AQ2-S4.6-03 | All cap-1/2/3 read tools still have `readOnlyHint: true` (regression) | test | same | live MCP client iterates and asserts |
| DOD-AQ2-S4.6-04 | `claim_next_job` MCP response is a 3-block content list (Job JSON + Packet JSON + text) | test | `artifacts/cap-04/mcp-claim-multipart.xml` | live MCP client asserts shape |
| DOD-AQ2-S4.6-05 | Race test (50 concurrent claimers, 1-Job pool): exactly 1 winner + 49 `no_ready_job`; total 50 audit rows | test | `artifacts/cap-04/race-50-concurrent.xml` | pytest asserts counts |
| DOD-AQ2-S4.6-06 | Race test (50 concurrent claimers, 5-Job pool): exactly 5 winners + 45 `no_ready_job`; no Job double-claimed | test | same | pytest asserts |
| DOD-AQ2-S4.6-07 | Claim atomicity: monkeypatched flush failure mid-tx → no Job state change, no audit row | test | `artifacts/cap-04/claim-atomicity.xml` | pytest asserts both counts |
| DOD-AQ2-S4.6-08 | Sweep per-Job atomicity: failure on Job N of 5-Job batch → Jobs 1..N-1 released-with-audit, Job N rolled back (still `in_progress`, no audit), Jobs N+1..5 untouched | test | `artifacts/cap-04/sweep-atomicity.xml` | pytest asserts per-Job invariant |
| DOD-AQ2-S4.6-09 | FastMCP multi-part output preflight spike confirms installed FastMCP version's API matches plan assumption (per Locked Decision 22); plan amended via 1-line clarification if signature differs | spike + artifact | `artifacts/cap-04/fastmcp-multipart-spike.txt` | spike output captures exact return type signature against the pinned FastMCP version; throwaway test deleted in same Story 4.6 commit cycle once real wiring lands |

**Depends on:** Stories 4.2, 4.3, 4.4, 4.5.

**Submission shape (ADR-AQ-030):** Sixth commit. `dod_results[]` = 8 entries. `handoff = "AQ2-S4.7 (Story 4.7 — Evidence pack + capabilities.md fix-up + C2)"`.

---

### Story 4.7 — Evidence pack + `capabilities.md` fix-up + C2 checkpoint

**Objective:** Run the full Docker test matrix, mypy `--strict`, ruff. Verify post-migration DB state. Commit the EXPLAIN evidence for the three locked query shapes. Apply the surgical `capabilities.md` fix-up (one commit, one file). Push the branch tip. Post comprehensive evidence on the cap-4 epic. C2 checkpoint fires here. **No PR yet** — Codex opens the PR after Ghost approves the evidence.

**Why this matters (human outcome):** The capability is done. Every DoD across stories 4.1–4.6 has artifact evidence. The `capabilities.md` prose matches the shipped reality (no stale CLI shorthand, no broken `describe_contract_profile` reference, accurate heartbeat-audit policy, accurate sweep audit shape, the new sweep env var documented). Mario reviews the evidence, approves, and Codex opens one PR for all of cap #4.

**Scope (in):**
- `plans/v2-rebuild/artifacts/cap-04/` — directory with all evidence artifacts referenced across stories 4.1–4.6 + this story's roll-up:
  - `evidence-summary.md` — narrative overview citing every DoD's artifact pointer.
  - `final-test-matrix.txt` — stdout of the full Docker pytest run.
  - `final-mypy-strict.txt` — stdout of `mypy --strict apps/api/src/aq_api/`.
  - `final-ruff.txt` — stdout of `ruff check apps/api apps/cli`.
  - `final-db-shape.txt` — `\d jobs`, `\d audit_log`, `SELECT count(*) FROM actors WHERE name='aq-system-sweeper'`, etc.
  - `cap04-locks-grep.txt` — grep evidence that all Locked Decisions 1–18 are reflected in code (e.g., `grep -rn "skip_success_audit" apps/api/src/aq_api/`).
- `plans/v2-rebuild/capabilities.md` (modify) — surgical fix-up:
  - **Line 264:** "`aq claim`" → "`aq job claim`"
  - **Line 265:** "`aq release`" → "`aq job release`"
  - **Line 267:** Replace "Audited per cap #2 rules (mutation = audit row); the audit row carries `target_id = job_id` and a minimal payload `{}` (no useful business state). Cross-claimant attempt → 403 with `error_code='heartbeat_forbidden'`, audit row recorded. Contract Profile sketched per ADR-AQ-030." with: "**Successful heartbeats do NOT write audit rows** (lease maintenance, not business history; the Job's `claim_heartbeat_at` column already stores the only state AQ needs). **Cross-claimant attempts → 403 with `error_code='heartbeat_forbidden'`, audit row recorded. Heartbeat on a non-`in_progress` Job → 409 with `error_code='job_not_in_progress'`, audit row recorded. Heartbeat on missing Job → 404 with `error_code='job_not_found'`, audit row recorded.** This is a documented deviation from cap #2's 'every mutation audits' rule, locked in `capability-04-plan.md` Locked Decision 5."
  - **Line 271** (`AQ_CLAIM_LEASE_SECONDS` paragraph): keep, append a new bullet: "**Sweep cadence:** `AQ_CLAIM_SWEEP_INTERVAL_SECONDS` (default `60`, range `[5, 3600]`). Server polls for stale claims at this cadence. Actual release latency for a dead claimant is bounded by `AQ_CLAIM_LEASE_SECONDS + AQ_CLAIM_SWEEP_INTERVAL_SECONDS`."
  - **Line 272** (auto-release sweep sentence): replace with: "Auto-release sweep: an in-process asyncio coroutine on the API process re-flips Jobs from `in_progress` to `ready` when `now() - claim_heartbeat_at > :lease`. Each auto-release writes an audit row with `op='claim_auto_release'`, `target_kind='job'`, `target_id=job_id`, `authenticated_actor_id` set to the reserved `aq-system-sweeper` actor (created idempotently at app startup AND seeded by migration `0006_cap04_indexes_and_system_actor`), `error_code='lease_expired'`, and `request_payload` containing `previous_claimant_actor_id` + `stale_claim_heartbeat_at` + `lease_seconds` for forensic continuity. Per-Job atomicity: each Job's reset and its audit row commit in one transaction; batch failures leave the invariant intact. Manual `reset_claim` stays — explicit human escape hatch."
  - **Line 281:** Replace the post-claim hint about `get_packet` with: "After a successful `claim_next_job`: the response includes a Context Packet stub (forward-compat with cap #8 — currently empty `previous_jobs[]` and `next_job_id: null`). Read the Job's inline `contract` field for the DoD, call `heartbeat_job` every ~30 seconds while working, and call `submit_job` (cap #5) when done. For now, use `release_job` if you can't complete the work."
  - **Line 290** (Packet hint text): "Required next: read the Contract Profile (`describe_contract_profile`) and the previous 2 Jobs in the Sequence." → "Required next: read the Job's inline `contract` field; call `heartbeat_job` every ~30s; `submit_job` ships in cap #5. The Packet's `previous_jobs[]` and `next_job_id` populate when cap #10's `sequence_next` edges land."
  - **Line 297** (validation summary): "`aq claim`" → "`aq job claim`" (3 occurrences); "`aq release`" → "`aq job release`" (1 occurrence); "`aq jobs ready`" verified against the cap-3 CLI shape and corrected if needed.
- AQ2-16 Plane comment: "Folded into cap-4 Story 4.5 (heartbeat sweep) per `capability-04-plan.md`. Ticket can be closed when cap-4 ships and cap-4 epic is closed."
- AQ2-17 Plane comment: "Folded into cap-4 Story 4.1 (`idx_jobs_in_progress_heartbeat`) and Story 4.6 (race test as the load-test analogue). The cap-3 partial btree `idx_jobs_state_project_created` already covers the claim path; cap-4 adds the sweeper-specific index. Ticket can be closed when cap-4 ships."

**Scope (out):**
- The PR itself (Codex opens after Ghost approval).
- Any new ops.

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T db psql -U aq -d aq2 -c "\d jobs"   # idx_jobs_in_progress_heartbeat present
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM actors WHERE name='aq-system-sweeper'"   # = 1
grep -n "aq job claim\|aq job release\|aq job heartbeat\|aq job reset-claim" plans/v2-rebuild/capabilities.md
grep -n "describe_contract_profile" plans/v2-rebuild/capabilities.md   # zero hits after fix-up
grep -n "AQ_CLAIM_SWEEP_INTERVAL_SECONDS" plans/v2-rebuild/capabilities.md   # at least one hit after fix-up
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4.7-01 | Full Docker test matrix passes (cap-1 + cap-2 + cap-3 + cap-3.5 + cap-4) | command | `artifacts/cap-04/final-test-matrix.txt` | green pytest output |
| DOD-AQ2-S4.7-02 | mypy `--strict` clean across `apps/api/src/aq_api/` | command | `artifacts/cap-04/final-mypy-strict.txt` | clean |
| DOD-AQ2-S4.7-03 | ruff clean across `apps/api apps/cli` | command | `artifacts/cap-04/final-ruff.txt` | clean |
| DOD-AQ2-S4.7-04 | `capabilities.md` cap-4 prose matches Locked Decisions 1–18 (no stale CLI shorthand, no `describe_contract_profile`, accurate audit shape, sweep env var documented) | grep | `artifacts/cap-04/capabilities-md-greps.txt` | all five greps from Verification above pass |
| DOD-AQ2-S4.7-05 | EXPLAIN evidence committed for all 3 locked query shapes (claim no-label, claim with labels, sweeper) | artifact | `artifacts/cap-04/explain-*.txt` (3 files) | each file shows the expected index access |
| DOD-AQ2-S4.7-06 | All artifacts under `artifacts/cap-04/` redacted via `scripts/redact-evidence.sh` before commit | command | `artifacts/cap-04/redaction-pass.txt` | gitleaks scan of artifacts dir clean |
| DOD-AQ2-S4.7-07 | AQ2-16 and AQ2-17 commented as folded; pre-existing Plane gap-tickets referenced from this evidence pack | command | `artifacts/cap-04/plane-gap-tickets-folded.txt` | comment text grep-verifiable on the two tickets |

**Depends on:** Stories 4.1, 4.2, 4.3, 4.4, 4.5, 4.6.

**Submission shape (ADR-AQ-030):** Seventh commit. `dod_results[]` = 7 entries. `handoff = "C2 checkpoint — evidence posted on cap-4 epic; awaiting Ghost approval. Codex opens PR after approval."`. **CHECKPOINT C2 fires here**.

---

## MCP richness for cap #4

Carries forward cap #2's locked pattern (Bearer + agent_identity decorative-only) and cap #3's annotations. Cap #4 ships the additions locked from this capability forward:

1. **`mcp.set_instructions(...)`** — the server-level block per Locked Decision 18 / Story 4.6's exact text.
2. **Tool annotations** — every cap-4 mutation: `{"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False}`. Heartbeat is technically idempotent on `claim_heartbeat_at` (calling it twice in the same second produces identical timestamps), but the lease semantics ARE state-changing from the agent's perspective ("I'm still working"), so it ships as `idempotentHint: False` for clarity.
3. **Tool descriptions** — auto-derived from Pydantic field docstrings; per-op "why-to-use / when-to-use" line in the MCP tool decorator's `description=` string. Example for `claim_next_job`: "Atomically claim the next `ready` Job from a Project's queue, optionally filtered by labels. Use this when your agent is ready to take on new work. Returns the Job + a Context Packet (currently a stub; cap #8 will populate it) + a next-step text hint. Call `heartbeat_job` while working, `release_job` to return the work, or `submit_job` (cap #5) when done."
4. **Output content bundling** — `claim_next_job` returns a 3-block FastMCP content list. The other three cap-4 ops (release, reset, heartbeat) return single Pydantic dumps (no multi-part needed; they don't carry navigation context).
5. **Tool input-schema field descriptions** — every Pydantic field in `ClaimNextJobRequest`, `ResetClaimRequest`, etc. carries a docstring; FastMCP auto-derives JSON Schema descriptions from those.

**Resources and Prompts** continue to be deferred:
- **Resources** land in cap #11 (Pipeline / ADR / Learning resources by URI). No cap-5 Contract Profile resources (profiles are dropped per cap-3.5).
- **Prompts** land in cap #6 dogfood (one prompt template `/aq-claim-and-work`).

---

## Hard checkpoints

- **C1 — after Story 4.2 lands.** `claim_next_job` works end-to-end across REST + CLI + MCP. Parity test green for `-k claim`. Race test deferred to Story 4.6, but the single-actor happy path + cross-project + label-filter cases all pass. Codex stops, posts evidence on cap-4 epic, awaits Ghost approval before Story 4.3.
- **C2 — after Story 4.7 lands.** All 7 stories complete. Full Docker stack healthy. 4 cap-4 ops + sweep covered by parity + race + atomicity tests. `capabilities.md` fix-up applied. Codex stops, posts evidence on cap-4 epic, awaits Ghost approval before opening PR.
- **PR open + Ghost merge approval.** Codex opens ONE PR (squash-merges cap-4 onto `main`). Awaits Ghost merge approval. Does NOT self-merge.

---

## Capability-level DoD list

The 60 DoD items embedded in stories 4.1–4.7 above plus these capability-wide DoDs:

| ID | Statement | Verification | Evidence |
|---|---|---|---|
| DOD-AQ2-CAP4-01 | All 4 cap-4 ops surface on REST + CLI + MCP with byte-equal payloads | parity test | `artifacts/cap-04/four-surface-equivalence.txt` |
| DOD-AQ2-CAP4-02 | Cap #1 + Cap #2 + Cap #3 + Cap #3.5 tests still pass unchanged | pytest | `artifacts/cap-04/regression-cap01-3.5.txt` |
| DOD-AQ2-CAP4-03 | `_assert_commit_matches_head()` invariant against authenticated `/version` on cap-4 branch tip | command | `artifacts/cap-04/commit-matches-head.txt` |
| DOD-AQ2-CAP4-04 | All cap-2 / cap-3 locks still present (sanity grep — `audited_op`, HMAC lookup_id, GIN index, partial-btree index) | grep | `artifacts/cap-04/carry-forward-locks.txt` |
| DOD-AQ2-CAP4-05 | No `/audit` Web view introduced (Pact lock — UI is read-only) | grep | `artifacts/cap-04/web-routes.txt` |
| DOD-AQ2-CAP4-06 | Cap #4 introduces exactly two new env vars: `AQ_CLAIM_LEASE_SECONDS`, `AQ_CLAIM_SWEEP_INTERVAL_SECONDS`; nothing else | command | `artifacts/cap-04/env-diff.txt` |
| DOD-AQ2-CAP4-07 | No Job ever enters `state='draft'` via cap #4 ops (per cap-3 F-P0-1 carry-forward; cap #4 has no draft entry path; cap-3.5 also creates `ready` Jobs, NOT `draft`) | DB query | `artifacts/cap-04/no-draft-jobs.txt` — `SELECT count(*) FROM jobs WHERE state='draft'` returns 0 across all live tests |
| DOD-AQ2-CAP4-08 | The reserved `aq-system-sweeper` actor is the only `kind='script'` actor with `name='aq-system-sweeper'`; cap-4 introduces no other special actor types | DB query | `artifacts/cap-04/system-actor-uniqueness.txt` |
| DOD-AQ2-CAP4-09 | Heartbeat-success-not-audited deviation is the ONLY case in cap-4 where a successful mutation skips the audit log; documented inline in `_audit.py` and `services/heartbeat.py` | grep | `artifacts/cap-04/skip-success-audit-uses.txt` |
| DOD-AQ2-CAP4-10 | `capabilities.md` cap-4 prose has zero `describe_contract_profile` references and zero top-level `aq claim` / `aq release` references | grep | `artifacts/cap-04/capabilities-md-cleanups.txt` |

---

## Validation summary

Run `scripts/validate-cap04.sh` end-to-end. The script:
1. `docker compose down --remove-orphans && docker compose build && docker compose up -d --wait` (NEVER `down -v`; preserves named volume).
2. `alembic upgrade head` then verify the new partial index + system actor row.
3. Bootstrap a founder via `aq setup`.
4. Walk the claim graph: create Project → register two labels (`area:web`, `area:api`) → create Pipeline → create 5 Jobs (3 with `area:web`, 2 with `area:api`) → `aq job claim --project <id> --label area:api` returns the oldest area:api Job, audit row records the filter → `aq job heartbeat <job-id>` updates `claim_heartbeat_at`, no audit row → `aq job release <job-id>` returns Job to `ready`, all claim fields cleared, audit row written → `aq job claim --project <id>` (no filter) returns the next FIFO Job → from a different actor's key, `aq job heartbeat <job-id>` returns 403 `heartbeat_forbidden` with audit row → `aq job reset-claim <job-id> --reason "claimant crashed"` returns Job to `ready` from the recovering actor's key, audit row records reason → claim again → set `AQ_CLAIM_LEASE_SECONDS=60` (test-only override); call `run_claim_auto_release_once(now=<70 seconds in the future>)` directly; assert Job returned to `ready` with audit row attributed to `aq-system-sweeper`.
5. Run all Docker pytest suites (`apps/api/tests`, `apps/cli/tests`, `tests/parity`, `tests/atomicity`).
6. Run the race test (50 concurrent claimers).
7. `EXPLAIN` the three locked query shapes; commit plans.
8. gitleaks v8.30.1 full-history scan.
9. `redact-evidence.sh` over every artifact before commit.

---

## Submission shape

- **Single branch** `aq2-cap-04` off `main` at `c956f1d`.
- **Story-by-story commits** (7 commits, each story = one commit).
- **ONE PR at the end.** Codex stops at C2 for Ghost evidence review, then opens one PR.
- **Each story closes its child ticket** via `plane_update_status` with closeout comment.
- **Strict ADR-AQ-030 evidence per story** under `plans/v2-rebuild/artifacts/cap-04/`, redacted via `scripts/redact-evidence.sh` before commit.
- **`capabilities.md` fix-up** rolls into Story 4.7's commit (NOT a separate doc PR — fix-up is part of the cap-4 PR per the plan-update-2026-04-28 amendment cadence).

---

## Risks / deviations (declared in submission)

1. **`capabilities.md` cap-4 prose has known stale text.** Lines 264, 265, 267, 271 (additive), 272, 281, 290, 297. Story 4.7 ships the surgical fix-up. The fix-up is part of the cap-4 PR, not a separate doc PR — agents reading the doc between merge of cap-3.5 and merge of cap-4 see the stale text; this is acknowledged. The `plan-update-2026-04-28*.md` files remain authoritative on conflict per the rev-4 banner.

2. **`audited_op` extension to carry `error_code` on success path — single-path lock per Locked Decision 21.** Story 4.5 extends `AuditOperation` with `error_code: str | None = None` and threads it through `record(...)` on the success path. `claim_auto_release` is implemented as a successful system mutation that sets `audit.error_code = 'lease_expired'` and exits the context manager normally — **NOT modeled as `BusinessRuleException` and NOT routed through the denial path** (both would roll back the state mutation, breaking same-transaction release-with-audit). This is a deviation from cap-2's implicit "error_code is set only on denials" pattern; documented as a code comment in `_audit.py` and as a one-line note in `capabilities.md`'s audit-log section (added in Story 4.7's fix-up if the section exists; otherwise filed as a follow-up gap-ticket).

3. **Heartbeat-success skips the audit log.** Locked Decision 5. The cap-2 invariant "every mutation always audits including denials with `error_code` set" technically still holds (every mutation route enters `audited_op`; denials still audit), but the success path uses `skip_success_audit=True`. Documented in code, in this plan, and in the `capabilities.md` fix-up.

4. **Sweep is an in-process coroutine, not `pg_cron`.** Locked Decision 6. Acknowledged tradeoff: simpler install path (no Postgres extension dependency for cap #12) at the cost of "the sweep runs in every Python worker process." v1 is single-instance per the Pact, so in v1 there is exactly one worker; future scale-out (v1.1+) would need a sweep coordination mechanism (advisory lock, leader election) — filed as a follow-up gap-ticket if/when scale-out arrives.

5. **No-ready-job audit row has `target_id=NULL`.** Locked Decision 12. The existing partial-WHERE index `audit_log_target_idx` does NOT cover these rows. Forensic queries for "denied claim attempts" must filter by `op + actor + ts`, not by `target_id`. This is intentional — denied no-row attempts have no Job to point at, and faking a `target_id` would lie about what the audit row represents.

6. **`AuditOperation.error_code` on success requires that all existing `audited_op` callers continue to default to `None`.** Story 4.4 + 4.5 ship a regression test (`test_audited_op.py`) asserting cap-1/2/3 routes still produce identical audit shapes. If a future capability assumes `error_code IS NULL ⟺ success`, that assumption breaks (cap-4's `claim_auto_release` is a counter-example). Future audit-redactor design must read `op + error_code` together, not `error_code` alone.

7. **Per-Job atomicity for the sweep, not per-batch.** Locked Decision 8. A failure on Job N of an N+5-Job batch does NOT roll back Jobs 1..N-1's releases. This means an operator running `SELECT count(*) FROM jobs WHERE state='in_progress' AND claim_heartbeat_at < now() - lease` mid-sweep may see a value between `total_stale_count` and `0` depending on timing. This is the right trade-off (per-Job atomicity is what agents observe; per-batch atomicity would force the sweep to be sequential, defeating SKIP LOCKED's parallelism gains).

8. **`ensure_system_actor` is race-safe via `IntegrityError` rollback + reselect** (Mario lock 3 / Locked Decision 20). Function pattern: SELECT active actor by name; if found, return UUID; else INSERT; on `IntegrityError` (concurrent winner inserted first), rollback and re-SELECT to fetch the winner's UUID. Tests cover four cases: (a) missing — inserts; (b) active row exists — no-op; (c) deactivated row exists — inserts new active row alongside (the partial-unique index permits multiple inactive + one active); (d) two concurrent calls race — both succeed, exactly one active row remains. Startup is robust to transient failure (per Locked Decision 20) — the lifespan logs and proceeds with `system_actor_id=None`; the sweep loop retries `ensure_system_actor` per iteration until it succeeds.

9. **No client-cadence enforcement.** The MCP `set_instructions` block recommends ~30s heartbeat cadence, but the server enforces only the 15-minute lease. An agent that heartbeats every 14 minutes is technically compliant but at high risk of clock-skew expiry. Documented as MCP guidance; if a future operator reports lost claims due to skew, file a gap-ticket for client-side cadence validation.

10. **Forward-compat empty Packet.** The `previous_jobs[]` and `next_job_id` fields are empty/null in cap #4. Cap #8 fills them in once cap #10's `sequence_next` edges exist. Agents reading the Packet must handle both shapes (cap #8 won't be a breaking change). The cap-3 precedent for empty arrays in `decisions`/`learnings` covers this pattern; cap-4 inherits it.

11. **Web tier has zero changes in cap #4.** The four cap-4 ops are API-only. Cap #11 owns UI. Parity tests skip Web assertions for the four cap-4 ops (existing pattern from cap-3).

---

## Ready for execution

Rev 1 incorporates the gate-1 brief approval (with 12 Codex corrections) and the gate-2 pre-plan approval (with 7 Mario tweaks):

- **Gate 1 (brief, 2026-04-28):** approved by Mario + Codex with 12 locked corrections that shaped the rev-1 plan.
- **Gate 2 (pre-plan, 2026-04-28):** approved by Mario with 7 tweaks (audited_op skip-success commit semantics, sweep-test contextvar ownership, parity timing Option A, sweep-interval range lock, no_ready_job audit shape, per-Job sweep atomicity, error-code lock table). All seven folded into the locks above.

Story-by-story execution discipline: Codex executes one story at a time, runs that story's verification commands, commits, then moves to the next. C1 (after Story 4.2) and C2 (after Story 4.7) are hard stops for `claude` audit before continuing. PR-open + Ghost merge approval is the final gate (no self-merge). This matches the cap-2 / cap-3 / cap-3.5 cadence that delivered cleanly.

Rev 1 plan is locked. Codex may begin Story 4.1 the moment Mario queues the cap-4 epic in Plane.
