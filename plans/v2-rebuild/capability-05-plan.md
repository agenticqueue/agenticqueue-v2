# Plan: AQ 2.0 Capability #5 — A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly

## Context

Cap #1 (four-surface ping), cap #2 (Authenticated Actors + Bearer auth + same-transaction audit log), cap #3 (entity CRUD), cap #3.5 (Workflow→Pipeline collapse + Contract Profile drop + inline `contract` JSONB), and cap #4 (atomic Job claim with heartbeat lease + auto-release sweep) are all on `main` at `7f65654` (post-AQ2-70 test-hygiene cleanup; current `origin/main` HEAD is `f64b7e8` after a Dependabot bump and the cap-5-prep handoff merge `f55c951`). After cap #4, an Actor can `claim_next_job`, hold the work via `heartbeat_job`, and let go via `release_job`/`reset_claim` or the auto-release sweep. **There is still no way to finish a Job.** Cap #5 closes that loop: a claimant calls `submit_job` with one of four outcomes, AQ shape-validates the payload against the Job's inline `contract` JSONB per ADR-AQ-030, transitions state, writes the audit row, and creates Decision and Learning nodes inline if `decisions_made[]` / `learnings[]` are non-empty — all in one transaction. `review_complete` lets any key resolve a `pending_review`. After cap #5 ships, cap #6 (Mario dogfoods one ticket) becomes meaningful.

This plan is **rev 2**, written after gate-1 (brief approved by Mario + Codex with 15 locks), gate-2 (codebase survey corrected two handoff assumptions), and gate-3 (Codex audit caught 5 P1 + 2 P2 implementer traps; all folded in below) on 2026-04-29. Every locked decision below is intentional and grep-verifiable; every story carries a "Why this matters (human outcome)" line; every DoD has a real verification command and an artifact path.

Cap #5 ships **2 ops** (`submit_job` + `review_complete`) plus **2 new tables** (`decisions`, `learnings`) plus **inline edge insertion** for `submit_job(outcome='blocked')` (writes a `job_edges(edge_type='gated_on')` row directly; no public `link_jobs` op — that's cap #10). Web tier is untouched per the Pact (cap #11 owns UI). The total MCP tool count rises from 33 → 35.

### Findings folded in from gate-1 audit (Codex + claude survey, 2026-04-29)

**Locked corrections — these are not re-litigated below; they shape the plan:**

- **C-1** Two new tables, symmetric shape per Codex Q1 lock: `decisions(id, attached_to_kind CHECK ('job','pipeline','project'), attached_to_id, title, statement, rationale NULL, supersedes_decision_id NULL FK decisions(id), created_by_actor_id FK actors(id), created_at, deactivated_at NULL)` and `learnings(id, attached_to_kind, attached_to_id, title, statement, context NULL, created_by_actor_id, created_at, deactivated_at NULL)`. Indexes `(attached_to_kind, attached_to_id, created_at)` and `(created_by_actor_id, created_at)` on both. Schema accepts all three `attached_to_kind` values; cap-5 submit only writes `'job'`.
- **C-2** No new edge-type enum values. `job_references_decision` and `job_references_learning` are NOT added by cap-5 — those belong to cap #9 / cap #10. Submit-time D&L attachment uses the `attached_to_*` columns only; the existing 3-value `job_edges.edge_type` CHECK (`gated_on`, `parent_of`, `sequence_next`) is unchanged. **Handoff §8.1's claim that these enum values were already in cap-3.5's migration was wrong** — verified at `apps/api/src/aq_api/models/db.py:307` where the CHECK still reads only the original three.
- **C-3** `submit_job(outcome='blocked')` persists the dependency by inserting one `job_edges(from_job_id=submitting_job_id, to_job_id=gated_on_job_id, edge_type='gated_on')` row inline in the same transaction as the state transition + audit row. **No `gated_on_job_id` column on `jobs`** (verified — `jobs` has no such column at `apps/api/src/aq_api/models/db.py:262-300`). No public `link_jobs` op exposed (cap #10 owns that). Validations: target Job exists, same project, not self. Cycle detection deferred to cap #10.
- **C-4** Validation is shape-only per cap #5 charter: outcome-specific required fields present + each `dod_results[].dod_id` matches `contract.dod_items[].id` + each status ∈ `{passed, failed, blocked, not_applicable}` + reject duplicate `dod_id` in results + for `outcome=done` every required DoD must be `passed` or `not_applicable` (NOT `failed`/`blocked`) + at least one `evidence` entry per `passed` result. ADR-AQ-030 conditional categories (`test_report`, `artifacts`, `sbom_and_provenance`, `reproducibility_package`, `failure_taxonomy`, `ui_verification`) are **not** validated at the cap-5 boundary — those are reviewer-check items.
- **C-5** `error_code=NULL` on all successful submit paths (including `outcome=failed`). `error_code` is reserved for denial paths only (Pydantic 422, claimant-mismatch 403, state-mismatch 409, contract-shape 422). Outcome encoded in `audit_log.response_payload`.
- **C-6** State machine completeness: the `jobs.state` CHECK constraint at `apps/api/src/aq_api/models/db.py:234-241` already permits all 8 values (`draft`, `ready`, `in_progress`, `done`, `failed`, `blocked`, `pending_review`, `cancelled`). Cap-5 introduces no schema change to `jobs.state`.
- **C-7** `cancel_job` interaction with `pending_review`: leave current behavior (allows cancel because `pending_review` is non-terminal — verified at `apps/api/src/aq_api/services/job_lifecycle.py:14`). Add an explicit test asserting this is deliberate.
- **C-8** `submit_job` response includes IDs of any Decision and Learning rows it created inline, so callers don't need a follow-up `list_decisions` round-trip.
- **C-9** Inheritance lookups in `get_job` / `get_pipeline` / `get_project` stay empty until cap #9. Cap-5 ships the tables only — does not touch the response shape (already `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` per cap-3 lock at `apps/api/src/aq_api/models/jobs.py:87-94` via `models/inheritance.py:6-8`).
- **C-10** Pipeline closure / "Pipeline done" lifecycle: **filed as AQ2-73** (discovery ticket, backlog) and **explicitly out of scope for cap #5**. Cap #5 closes the **Job** loop only; Pipeline-level rollup is decided before cap #6 dogfood per AQ2-73.

**Tweaks from the codebase survey (these are the rev-1 deltas vs the handoff):**

- **T-1** Migration is `0007_cap05_decisions_and_learnings`, chained off `0006_cap04_indexes_and_system_actor`. One Alembic revision creates both tables + indexes.
- **T-2** Pydantic submission shape is a discriminated union on `outcome` (Pydantic 2 `Discriminator(...)`); one variant per outcome, all `extra='forbid', frozen=True`.
- **T-3** Story 5.6's race test phrasing locked: "50 concurrent submit attempts against the same `in_progress` Job — exactly one wins, 49 see `409 job_not_in_progress` or `403 submit_forbidden`." NOT a 50-claimant race (cap-4 already covered that).
- **T-4** Sweep-vs-submit race: if the sweep wins (auto-release fires while submit is mid-tx), submit returns `409 job_not_in_progress` with **zero partial state**: no D/L rows inserted, no Job state change. Atomicity test asserts row counts before/after.
- **T-5** No separate spike story. Story 5.5's atomicity test is the proof: "job state transition + N decision inserts + N learning inserts + 1 audit row commit-or-rollback together."
- **T-6** Test fixtures that touch `actors`, `audit_log`, `decisions`, or `learnings` use `apps/api/tests/_isolated_schema.py` (cap-4 lock; verified at `apps/api/tests/_isolated_schema.py:26-72`).

### Findings folded in from gate-3 audit (Codex P1+P2 review, 2026-04-29)

**Codex's P1 findings — all locked into the rev-2 plan below:**

- **P1-1** **`submit_job` clears claim fields on every successful outcome.** Originally the plan transitioned `state` only, leaving `claimed_by_actor_id` / `claimed_at` / `claim_heartbeat_at` populated on terminal Jobs. That's stale lease metadata — wrong. Mirrors cap-4 LD 10's `release_job` / `reset_claim` / `claim_auto_release` pattern. `review_complete` does NOT touch claim fields (the prior `submit_job(outcome=pending_review)` already cleared them). See LD 7.
- **P1-2** **`audit_row_id` removed from `SubmitJobResponse`.** Original spec wanted the response to echo the audit row's UUID, but `audited_op` writes the success audit AFTER the service block exits and doesn't expose the inserted ID back. Self-reference inside `response_payload` was circular too. Cap #7's `list_runs`/`get_run` lets callers find their submission's audit row by `target_id + created_at` ordering. See LD 4. DOD-AQ2-S5.2-03 dropped.
- **P1-3** **`BusinessRuleException` extended to carry `details: Mapping[str, object] | None = None`.** Original `_audit.py:11-15` exception only has `status_code/error_code/message`; routes serialize as `{"error": exc.error_code}`. Cap-5's `contract_violation` denials need to name the offending field — Story 5.2 ships the extension as part of its scope, threading `details` through to the route response body AND the audit `response_payload`. See LD 23.
- **P1-4** **Per-outcome validation rules restated explicitly.** Original LD 5 said "every `dod_results[].dod_id` must match contract" universally, but Story 5.3 said "failed skips dod_id matching" — contradiction. Locked: `done` requires every contract DoD covered + only `passed`/`not_applicable` statuses; `pending_review` requires dod_id match but allows any of the 4 statuses; `failed` accepts empty `dod_results` and if non-empty still requires dod_id match (just allows non-terminal statuses); `blocked` excludes `dod_results` from the Pydantic model entirely (`extra='forbid'` rejects it). See LD 5.
- **P1-5** **Validation order locked: 404 → 409 (state) → 403 (auth) → 422 (`contract_violation`).** Original plan had auth before state, which would turn terminal/cleared-claim cases into `403 submit_forbidden` instead of the locked `409 job_not_in_progress`. With P1-1's claim-field clearing, the only Jobs with a populated claimant are `in_progress` ones — fetching the row, checking state first, then auth, gives the right error code at every step. See LD 5.

**Codex's P2 findings — fixed in story scope:**

- **P2-1** **Duplicate `gated_on` edge test pre-seeds the existing edge.** Original Story 5.3 plan said "submit blocked, reset_claim, claim again, submit blocked" — impossible because `reset_claim` only works on `in_progress` (a `blocked` Job can't be reset). Lock: pre-seed `INSERT INTO job_edges` directly in the test fixture, then claim Job A + submit blocked with `gated_on_job_id=B` → 409 `gated_on_already_exists`. See Story 5.3.
- **P2-2** **Story 5.2 ships D&L inline-creation; Story 5.5 is pure atomicity tests.** Original split had Story 5.2 silently dropping `decisions_made[]` / `learnings[]` if non-empty (returning `created_decisions=[]` regardless of input). Codex flagged: accepting input + dropping it fails audit. Locked: Story 5.2 ships the COMPLETE `submit_job(outcome=done)` including D&L inserts; Story 5.5 contains failure-injection atomicity tests only (no new feature work). See revised story breakdown.

### Why cap #5 matters (human outcome)

After cap #5 ships, AQ 2.0's loop closes. The human or agent can:
- Claim a Job (cap #4), do the work, and call `aq job submit <job-id> --outcome done --payload @closeout.json` to terminate it. The Job transitions to `done`; the audit log records the submission; the agent moves on to the next claim.
- Submit `outcome=failed` when the work hits a wall and explain why; the Job lands in `failed` (terminal) without polluting the queue.
- Submit `outcome=blocked` with a `gated_on_job_id`; the Job lands in `blocked` and AQ writes a `gated_on` edge in the same transaction. Cap #10 will later auto-promote it back to `ready` when the gating Job completes.
- Submit `outcome=pending_review` when the work needs a human's eyes before terminal; any other key can later call `aq job review-complete <job-id> --final-outcome done|failed` to resolve it.
- Capture Decisions and Learnings inline at submit time (`decisions_made[]` / `learnings[]` arrays on the payload). Each non-empty entry becomes a row in the new `decisions` / `learnings` tables, attached to the submitting Job, all in one transaction. Cap #9 will later wire standalone D&L ops + the inheritance lookups in `get_*`.
- Trust that submit and the auto-release sweep cannot interleave to produce partial state — if the sweep wins the race, submit fails atomically with no D/L row leakage.

What cap #5 deliberately does **not** ship: Pipeline closure (AQ2-73 discovery), DoD execution / runtime test invocation (cap-5 is shape-only — Contracts are schemas, not CI), `gated_on` auto-resolution (cap #10), automatic D&L surfacing in Context Packets or `get_*` responses (cap #9 wires those), the standalone D&L ops `create_decision` / `submit_learning` / `list_decisions` / `list_learnings` / `get_decision` / `get_learning` / `supersede_decision` / `edit_learning` (all cap #9), the `link_jobs` public op (cap #10), or any Web view (cap #11).

---

## Hard preconditions (must be on `main` before cap #5 first commit)

| Ticket | Title | Status |
|---|---|---|
| AQ2-39 | Capability #3 epic | **DONE** ✓ |
| AQ2-54 | Capability #3.5 epic | **DONE** ✓ |
| AQ2-62 | Capability #4 epic | **DONE** ✓ (`de1febd`) |
| AQ2-70 | Test-hygiene fixture isolation cleanup | **DONE** ✓ (`7f65654`) |
| AQ2-73 | Pipeline closure discovery | filed (backlog) — **not blocking cap #5** |

Cap-4 + AQ2-70 squash-merge to `main` at `7f65654` is the cap-5 starting point. No additional preconditions.

---

## Capability statement (verbatim from `capabilities.md`, with pending fix-up note)

> **Capability #5: A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly.** A claimant calls `submit_job` with `outcome ∈ {done, pending_review, failed, blocked}` and a structured payload; AQ validates the payload against the Job's inline `contract` JSONB per ADR-AQ-030, checks state transition validity, commits the new state and audit row in one transaction, and (per `plan-update-2026-04-28.md` Decision 4) creates Decision and Learning nodes inline if `decisions_made[]` or `learnings[]` are non-empty. Pending-review Jobs can be transitioned to `done` or `failed` by any key via `review_complete`.

A surgical fix-up commit lands alongside Story 5.7 to amend the cap-5 prose in `capabilities.md` for stale `register_contract_profile` / `version_contract_profile` references (op was cancelled in cap-3.5 / AQ2-50 per Decision 3) and the missing inline-D&L-at-submit semantics. See "Risks / deviations" item 1 for exact line ranges.

**Depends on:** Cap #4 (claim must exist before submit can fire); cap #3.5 (inline `contract` JSONB on every Job); cap #3 (`get_job`/`get_pipeline`/`get_project` already return empty `decisions` / `learnings` arrays for cap-9 forward-compat).

---

## Locked decisions for cap #5

These are cap #5-specific commitments **beyond** what cap #1, cap #2, cap #3, cap #3.5, and cap #4 already locked. Every story below honors all of them.

1. **One Alembic migration revision** (`0007_cap05_decisions_and_learnings`) ships the entire schema delta:
   - `CREATE TABLE decisions (id UUID PK, attached_to_kind TEXT NOT NULL CHECK ('job','pipeline','project'), attached_to_id UUID NOT NULL, title TEXT NOT NULL, statement TEXT NOT NULL, rationale TEXT NULL, supersedes_decision_id UUID NULL REFERENCES decisions(id) ON DELETE RESTRICT, created_by_actor_id UUID NOT NULL REFERENCES actors(id) ON DELETE RESTRICT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deactivated_at TIMESTAMPTZ NULL)`.
   - `CREATE TABLE learnings (id UUID PK, attached_to_kind TEXT NOT NULL CHECK ('job','pipeline','project'), attached_to_id UUID NOT NULL, title TEXT NOT NULL, statement TEXT NOT NULL, context TEXT NULL, created_by_actor_id UUID NOT NULL REFERENCES actors(id) ON DELETE RESTRICT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deactivated_at TIMESTAMPTZ NULL)`.
   - Indexes on each: `idx_<table>_attached(attached_to_kind, attached_to_id, created_at)` and `idx_<table>_actor(created_by_actor_id, created_at)`.
   - **No CHECK on `attached_to_id` referential integrity** (the column is polymorphic across `jobs` / `pipelines` / `projects`; FK enforcement is application-level at insert time, mirroring how cap-3.5 handles `job_edges.from_job_id` / `to_job_id` cross-type references).
   - **No new edge-type enum values.** The `job_edges.edge_type` CHECK at `db.py:307` stays at `('gated_on','parent_of','sequence_next')`.
   - Round-trippable: `alembic upgrade head → downgrade -1 → upgrade head` produces identical schema.

2. **`submit_job` ops in one transaction.** A single explicit transaction wraps:
   - SELECT the Job FOR UPDATE (verifies state + claimant in row-locked tx)
   - Pydantic + business validation (rejects raise `BusinessRuleException`)
   - UPDATE the Job's state
   - For `outcome='blocked'`: INSERT one row into `job_edges(from=submitter, to=gated_on_job_id, edge_type='gated_on')`
   - For each `decisions_made[]` entry: INSERT one row into `decisions` with `attached_to_kind='job', attached_to_id=submitter`
   - For each `learnings[]` entry: INSERT one row into `learnings` with `attached_to_kind='job', attached_to_id=submitter`
   - Audit row written via `audited_op` success path
   - One final commit covers all the above. Failure of ANY insert rolls back the whole submission.

3. **Discriminated union for `SubmitJobRequest`.** Pydantic 2 `Discriminator('outcome')` with one variant per outcome:
   - `SubmitJobDoneRequest{outcome: Literal['done'], dod_results, commands_run, verification_summary, files_changed, risks_or_deviations, handoff, learnings, decisions_made}`
   - `SubmitJobPendingReviewRequest{outcome: Literal['pending_review'], submitted_for_review, dod_results, commands_run, verification_summary, files_changed, risks_or_deviations, handoff, learnings, decisions_made}`
   - `SubmitJobFailedRequest{outcome: Literal['failed'], failure_reason, dod_results, commands_run, verification_summary, files_changed, risks_or_deviations, handoff, learnings, decisions_made}` (`dod_results` / `commands_run` / `verification_summary` allowed empty for failed; reason required)
   - `SubmitJobBlockedRequest{outcome: Literal['blocked'], gated_on_job_id, blocker_reason, files_changed, risks_or_deviations, handoff, learnings, decisions_made}`
   - All four `extra='forbid', frozen=True` per cap-1 lock. UTC datetimes via `aq_api._datetime` (cap-1 carry-forward).
   - `learnings` and `decisions_made` are typed as `list[SubmitLearningInline]` / `list[SubmitDecisionInline]` (inline submission shapes — `title` + `statement` + `rationale?` for decisions; `title` + `statement` + `context?` for learnings — NOT IDs of pre-existing rows; cap-5 creates these rows from scratch).

4. **`SubmitJobResponse` shape locked:**
   ```
   SubmitJobResponse {
     job: Job,                                    # post-transition state, claim fields cleared per LD 7
     created_decisions: list[UUID],               # IDs of decision rows created inline (empty if decisions_made was empty)
     created_learnings: list[UUID],               # IDs of learning rows created inline
     created_gated_on_edge: bool                  # true iff outcome=blocked AND edge was inserted
   }
   ```
   Per Codex Q10: callers receive immediate confirmation of created D/L IDs without a follow-up round-trip. Per gate-3 P1-2: `audit_row_id` is NOT included — `audited_op` writes the success audit row after the service block exits and the ID is not exposed back; cap #7's `list_runs`/`get_run` (querying `audit_log` by `target_id + ts`) is the supported lookup path.

5. **Validation rules locked (shape-only) — apply in this exact order at the boundary** (per gate-3 P1-5):
   1. **Pydantic-level (422, NOT audited; request never reaches the service):** field presence per outcome variant, type checks, `extra='forbid'` rejections, `gated_on_job_id` presence under `blocked`, `final_outcome ∈ {done, failed}` for `review_complete`.
   2. **Existence (404, audited):** Job must exist — error_code `job_not_found`.
   3. **State (409, audited):** Job's state must equal `in_progress` (or `pending_review` for `review_complete`) — error_code `job_not_in_progress` (or `job_not_pending_review`). Fires BEFORE auth so a terminal/cleared-claim Job cleanly returns 409 instead of 403. Per LD 7, claim fields are NULL on terminal Jobs, so a "wrong actor" check on a terminal Job would resolve the wrong error otherwise.
   4. **Authorization (403, audited):** Job's `claimed_by_actor_id` must equal the authenticated actor — error_code `submit_forbidden` (only meaningful on `in_progress` Jobs since cap-5 just verified state).
   5. **Contract shape (422, audited):** the per-outcome rule set below.

   **Per-outcome contract validation** (per gate-3 P1-4):

   | Outcome | dod_id ⊆ contract.dod_items[].id? | All contract DoDs covered? | Allowed statuses | duplicates? | Evidence req? |
   |---|---|---|---|---|---|
   | `done` | required (every result's dod_id MUST be in contract; every contract dod_id MUST be in results) | YES | only `passed` or `not_applicable` (NOT `failed`/`blocked`) | rejected | every `passed` needs ≥1 `evidence` entry |
   | `pending_review` | required (every result's dod_id in contract) | NO (partial review fine) | any of 4 | rejected | every `passed` needs ≥1 evidence |
   | `failed` | required IF `dod_results[]` non-empty (empty array is valid; partial reporting allowed) | NO | any of 4 | rejected | not enforced (failures may report without evidence) |
   | `blocked` | N/A — `dod_results` is NOT a field of `SubmitJobBlockedRequest` (Pydantic `extra='forbid'` rejects it at boundary) | N/A | N/A | N/A | N/A |

   Violations raise `BusinessRuleException(422, 'contract_violation', message=..., details={'rule': '...', 'dod_id': '...', 'expected': '...', 'got': '...'})` with `details` populated per LD 23. Specific rule sub-codes inside `details.rule`: `dod_id_unknown`, `duplicate_dod_id`, `incomplete_dod`, `missing_required_dod`, `no_evidence`. Per Codex Q4 lock.

   **Outcome-specific extra checks:**
   - `outcome='blocked'`: `gated_on_job_id` referenced Job must exist (else 409 `gated_on_invalid:not_found`), share `project_id` with submitter (else 409 `gated_on_invalid:cross_project`), and not equal submitter (else 409 `gated_on_invalid:self`). Per LD 12.

   **NO conditional ADR-AQ-030 categories** are validated. `test_report`, `artifacts`, `sbom_and_provenance`, `reproducibility_package`, `failure_taxonomy`, `ui_verification`, `diff_url`, `followup_ticket_id` are NOT required at the cap-5 boundary. Reviewer-check material only.

6. **`error_code` semantics: success path always NULL.** Per Codex Q8: `submit_job` and `review_complete` writes a success audit row with `error_code=NULL` regardless of whether `outcome='done'` or `outcome='failed'`. Outcome lives in `audit_log.response_payload->>'outcome'`. `error_code` is reserved for denial paths (Pydantic 422 / 403 / 409 / 404 / `contract_violation`).

7. **State transitions + claim-field clearing locked (per gate-3 P1-1):**
   - `submit_job(outcome='done')`: `in_progress → done` (terminal). Single UPDATE sets `state='done'`, `claimed_by_actor_id=NULL`, `claimed_at=NULL`, `claim_heartbeat_at=NULL`.
   - `submit_job(outcome='failed')`: `in_progress → failed` (terminal). Same claim-field clearing.
   - `submit_job(outcome='blocked')`: `in_progress → blocked` (non-terminal; cap #10 promotes back to `ready` on gate resolution). Same claim-field clearing.
   - `submit_job(outcome='pending_review')`: `in_progress → pending_review` (non-terminal). Same claim-field clearing.
   - `review_complete(final_outcome='done')`: `pending_review → done` (terminal). State UPDATE only; **claim fields stay NULL** (already cleared by the prior `submit_job(outcome=pending_review)`).
   - `review_complete(final_outcome='failed')`: `pending_review → failed` (terminal). Same — state-only update.
   - **Every successful `submit_job` clears all three claim fields in the same UPDATE.** Mirrors cap-4 LD 10's `release_job` / `reset_claim` / `claim_auto_release` pattern. No stale lease metadata on terminal or non-terminal-but-not-claimed Jobs.
   - **Cap-5 NEVER transitions to `ready`** (that's cap-4 release/reset/sweep + cap-10's `gated_on` resolver).
   - **Cap-5 NEVER transitions out of `cancelled`/`done`/`failed`** (terminal — submit on a terminal Job returns 409 `job_not_in_progress`).
   - `cancel_job`'s allowed-source-state set is unchanged (verified at `services/job_lifecycle.py:14` — only `done`/`failed`/`cancelled` rejected; `pending_review`/`blocked` remain cancellable).

8. **`review_complete` is any-actor.** Mirrors `reset_claim` (cap-4) — any valid Bearer key may resolve a `pending_review`. The audit row records the reviewing actor's `authenticated_actor_id`. `final_outcome ∈ {done, failed}` (NOT `pending_review` / `blocked` — those don't make sense as second-pass states).

9. **`audited_op` four-path semantics (carried from cap-4) — unchanged.** Cap-5 uses the **success-with-normal-audit** path for both `submit_job` and `review_complete`. No `skip_success_audit=True` in cap-5 (every submission is business history). Denial paths use `BusinessRuleException` per existing pattern at `apps/api/src/aq_api/_audit.py:39-56`.

10. **Audit row shape locked for cap-5 ops:**
    ```
    submit_job success:
      op = 'submit_job'
      target_kind = 'job'
      target_id = job_id
      authenticated_actor_id = claimant
      error_code = NULL
      request_payload = full submission payload (post-redaction)
      response_payload = {outcome, created_decisions: [...], created_learnings: [...], created_gated_on_edge: bool, audit_row_id (self-ref echoed for forward-compat with cap #7)}

    review_complete success:
      op = 'review_complete'
      target_kind = 'job'
      target_id = job_id
      authenticated_actor_id = reviewing actor (NOT necessarily the original claimant)
      error_code = NULL
      request_payload = {final_outcome, review_notes?}
      response_payload = {final_outcome, prior_state: 'pending_review'}
    ```

11. **Error code lock table** (every cap-5 op + every state-mismatch case):

    | Op | Condition | Status | error_code | Audited? | DoD |
    |---|---|---|---|---|---|
    | `submit_job` | success (any outcome) | 200 | NULL | yes (success) | DOD-AQ2-S5.2-01, S5.3-01..03 |
    | `submit_job` | Pydantic invalid (missing field, bad enum, extra field) | 422 | (Pydantic) | not audited | DOD-AQ2-S5.2-09 |
    | `submit_job` | Job not found | 404 | `job_not_found` | yes (denial) | DOD-AQ2-S5.2-10 |
    | `submit_job` | caller ≠ claimant | 403 | `submit_forbidden` | yes (denial) | DOD-AQ2-S5.2-11 |
    | `submit_job` | state ≠ `in_progress` (terminal or non-claimed) | 409 | `job_not_in_progress` | yes (denial) | DOD-AQ2-S5.2-12 |
    | `submit_job` | `dod_id` mismatch / duplicate / bad status / missing required DoD under `done` / no evidence under `passed` | 422 | `contract_violation` | yes (denial; details object names offending field) | DOD-AQ2-S5.2-13..16 |
    | `submit_job(blocked)` | `gated_on_job_id` not found OR cross-project OR self | 409 | `gated_on_invalid` | yes (denial) | DOD-AQ2-S5.3-08, S5.3-09 |
    | `review_complete` | success | 200 | NULL | yes (success) | DOD-AQ2-S5.4-01 |
    | `review_complete` | state ≠ `pending_review` | 409 | `job_not_pending_review` | yes (denial) | DOD-AQ2-S5.4-04 |
    | `review_complete` | Job not found | 404 | `job_not_found` | yes (denial) | DOD-AQ2-S5.4-05 |
    | `review_complete` | `final_outcome` not in `{done, failed}` (Pydantic) | 422 | (Pydantic) | not audited | DOD-AQ2-S5.4-06 |

12. **`gated_on` edge insertion locked** (per Codex Q3, option A):
    ```sql
    INSERT INTO job_edges (from_job_id, to_job_id, edge_type)
    VALUES (:submitting_job_id, :gated_on_job_id, 'gated_on')
    ```
    Inside the same `audited_op` block as the state transition. The `job_edges` PK constraint (`PRIMARY KEY (from_job_id, to_job_id, edge_type)` at `db.py:310-315`) prevents duplicate edges — a second `submit_job(blocked)` with the same `gated_on_job_id` returns `IntegrityError` → re-raise as `BusinessRuleException(409, 'gated_on_already_exists', ...)`. Per Codex: cycle detection deferred to cap #10. `from_job_id`'s referenced Job is the submitter (about to land in state `blocked`); `to_job_id`'s referenced Job is the gating Job (any state — cap-5 only requires existence + same project, NOT that it's claimable). `created_gated_on_edge` field of the response is set to `true`; for any other outcome it's `false`.

13. **D&L inline-create semantics locked:**
    - For each entry in `decisions_made[]`: INSERT one row into `decisions` with `attached_to_kind='job', attached_to_id=submitting_job_id, title=<entry.title>, statement=<entry.statement>, rationale=<entry.rationale or NULL>, supersedes_decision_id=NULL, created_by_actor_id=<authenticated_actor_id>, deactivated_at=NULL`. `created_at` defaults to `now()`. Returned ID added to `created_decisions[]`.
    - For each entry in `learnings[]`: same pattern with `learnings` columns.
    - **Cap-5 does NOT support `supersedes_decision_id` at submit time** — the field is in the schema for cap #9's standalone `supersede_decision` op; submit-time decisions are always net-new.
    - **Cap-5 does NOT write `job_references_decision`/`job_references_learning` edges** (per C-2 — those don't exist in the enum).
    - Inserts run BEFORE the audit row is written (so `response_payload.created_decisions/created_learnings` are populated). All inserts are inside the same `audited_op` block — failure of any insert raises and rolls back the whole transaction (state transition + earlier inserts + audit row all undone).

14. **CLI under `aq job` group** (per cap-4 LD 14 carry-forward + Codex Q12). Two new commands:
    - `aq job submit <job-id> --outcome <enum> --payload @file.json` (or `--payload '{"...": "..."}'`)
    - `aq job review-complete <job-id> --final-outcome <done|failed> [--notes "..."]`
    No top-level `aq submit` / `aq review-complete` aliases.

15. **Parity-test timing — Option A (incremental, per cap-4 LD 15).** Stories 5.2, 5.3, 5.4 each regenerate `tests/parity/openapi.snapshot.json` and `tests/parity/mcp_schema.snapshot.json` for their own ops. C1 (after Story 5.2) requires parity green for `submit_job(outcome=done)`. Story 5.6 ships **MCP richness refinement** + **race + atomicity tests**, NOT snapshot regeneration. Each push has clean snapshots.

16. **`capabilities.md` fix-up commit** lands alongside Story 5.7 (the C2 evidence pack). Surgical edits to lines 304, 315, 318-323, 325 (the cap-5 prose). See "Risks / deviations" item 1 for exact replacement text.

17. **EXPLAIN evidence required** for two query shapes, committed under `plans/v2-rebuild/artifacts/cap-05/`:
    - `explain-decisions-attached-lookup.txt` — `SELECT ... FROM decisions WHERE attached_to_kind='job' AND attached_to_id=:job_id ORDER BY created_at`, asserts `idx_decisions_attached` use.
    - `explain-learnings-attached-lookup.txt` — same pattern for `learnings`. (Forward-compat verification for cap #9's inheritance lookups.)

18. **MCP richness pattern (carried from cap-4 LD 18)**:
    - `submit_job` + `review_complete` annotations: `{"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False}`. Both are state-changing terminal-or-near-terminal transitions.
    - Tool descriptions auto-derived from Pydantic field docstrings + a per-op "why-to-use / when-to-use" line. `submit_job`'s description spells out the four outcomes and the per-outcome required fields.
    - `submit_job` returns a multi-part FastMCP content list: Job JSON + audit-row reference JSON + text block per cap-4 precedent. `review_complete` returns single Job JSON dump (no navigation context to bundle).
    - Server-instructions block updated to reference `submit_job` / `review_complete` as the canonical exit paths (replaces cap-4's "submit_job ships in cap #5 — use release_job for now" text).

19. **`recommended_review_after_seconds` is NOT introduced.** Unlike cap-4's `recommended_heartbeat_after_seconds` constant, cap-5 has no comparable client-cadence guidance. Reviewer cadence is human-driven; `pending_review` Jobs sit until someone calls `review_complete`. No env var, no constant, no MCP-instructions text on this.

20. **Per-Job atomicity invariant (carries cap-4 LD 8 pattern).** Each `submit_job` call is exactly one transaction containing: state UPDATE + (optional) `gated_on` edge INSERT + (0..N) decision INSERTs + (0..N) learning INSERTs + audit row INSERT. Failure of ANY of these rolls back ALL of them. No partial state under any failure mode (DB error, race with sweep, FK violation on `gated_on_job_id`, IntegrityError on duplicate `gated_on` edge).

21. **`audited_op.error_code` field stays unchanged from cap-4.** Cap-5 does NOT add new audit-shape fields; cap-4's `AuditOperation.error_code` field at `apps/api/src/aq_api/_audit.py:18-23` is the only success-path-with-diagnostic-code consumer in v1 (`claim_auto_release`). Cap-5 uses `error_code=None` on success per LD 6.

22. **MCP submit_job preflight spike — NOT REQUIRED.** Cap-4 LD 22 spiked FastMCP multi-part output once; the precedent stands. Cap-5 reuses the spike's confirmed return type. If FastMCP version pin changes between cap-4 merge and cap-5 first commit, Story 5.6 includes a 5-line smoke-test re-spike before wiring `submit_job`'s multi-part response (added to Story 5.6 scope as a guard, not a blocking story). Track via `pyproject.toml` FastMCP pin diff at branch start.

23. **`BusinessRuleException` + route error serialization extended for `details`** (per gate-3 P1-3). Story 5.2 ships the foundational extension as part of its scope:
    - `apps/api/src/aq_api/_audit.py`: extend `BusinessRuleException` constructor signature to `__init__(self, *, status_code, error_code, message, details: Mapping[str, object] | None = None)`. Default `None` preserves cap-1/2/3/4 callers' identical behavior.
    - `apps/api/src/aq_api/_audit.py`: `audited_op` denial path (existing lines 39-56) merges `exc.details` into `response_payload` if set. Specifically: `response_payload = audit.response_payload or {"error": exc.error_code, **({"details": exc.details} if exc.details else {})}`.
    - `apps/api/src/aq_api/routes/_errors.py` (or wherever the `BusinessRuleException` exception handler lives — verify location during Story 5.2; the handler currently returns `{"error": exc.error_code}`). Extend to return `{"error": exc.error_code, "details": exc.details}` ONLY when `details` is set; absent details preserves `{"error": exc.error_code}` exactly.
    - MCP tool error serialization mirrors REST: errors come back as `{error_code, rule_violated, details?}` per cap-4 server-instructions text.
    - Regression test in Story 5.2: every existing cap-1/2/3/4 denial path still produces identical error JSON (no `details` key when not set). One representative denial per cap (cap-2 `revoke_api_key` cross-actor 403, cap-3 `update_job` reject-state 400, cap-4 `release_job` wrong-claimant 403) is asserted byte-equal to its pre-cap-5 shape.
    - Cap-5 callers that USE `details`: `contract_violation` (LD 5 sub-rule + `dod_id` + `expected`/`got`), `gated_on_invalid` (sub-rule: `not_found` / `cross_project` / `self`), `gated_on_already_exists` (no details — the duplicate is self-explanatory).

---

## Carry-forward locked rules from caps #1, #2, #3, #3.5, #4

Every cap #5 story honors all of these. Repeated for grep-recall, not re-litigation.

**From cap #1:**
- Z-form datetime via `aq_api._datetime.parse_utc`. All timestamps timezone-aware UTC.
- Single Pydantic source of truth — no surface re-declares contract.
- Real-stack validation: `docker compose down && up -d --build --wait` + `_assert_commit_matches_head()`.
- Strict ADR-AQ-030 evidence — every artifact under `plans/v2-rebuild/artifacts/cap-05/`, redacted via `scripts/redact-evidence.sh` before commit.
- Four-surface parity discipline: REST + CLI + MCP + Web (Web no-op for cap #5 since no new views).

**From cap #2:**
- All API + MCP handlers `async def`. Never sync.
- Postgres 16-alpine, internal-only network, `aq2_pg_data` named volume.
- SQLAlchemy 2.x async (asyncpg) at runtime; psycopg sync for Alembic only.
- In-process service layer: REST + MCP handlers call the same Python service functions.
- MCP HTTP requires caller's Bearer; no bridge actor; `agent_identity` decorative-only.
- Reads NEVER audited; mutations ALWAYS use `audited_op`.
- Same-transaction audit guarantee.
- Three-layer secret redaction: regex-recursive in app + `scripts/redact-evidence.sh` for artifacts + gitleaks workflow.
- HMAC-SHA256 lookup_id is the auth lookup primitive.

**From cap #3 + cap #3.5:**
- Pipelines are one entity type with `is_template` BOOLEAN; archived via `archived_at`.
- Every Job carries an inline `contract JSONB NOT NULL` — cap-5 reads this for shape validation.
- `update_job` is metadata-only (rejects `state`, `labels`, `contract`, claim fields per `services/jobs.py:33-42`). Cap-5 does NOT touch `update_job`.
- `cancel_job` (cap-3) state set unchanged; `pending_review` and `blocked` remain cancellable.
- `get_job`/`get_pipeline`/`get_project` already ship empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` — cap-5 does NOT modify the response shape.

**From cap #4:**
- `audited_op` four-path semantics; `AuditOperation.error_code` field on success path.
- `aq job <verb>` CLI grouping (no top-level aliases).
- MCP richness pattern: server-instructions, tool annotations, multi-part output for the navigation-y op.
- `_isolated_schema.py` test fixture pattern for any test mutating shared tables.
- Auth session lifetime: short-lived auth session via `async with SessionLocal()`; release before route work begins (cap-4 lock per `_auth.py`).
- `claim_heartbeat_at` is the lease-tracking column; cap-5 reads it for the sweep-vs-submit race test (Story 5.6).

---

## Out of scope (explicit forbids)

Repeated from `capabilities.md` cap #5 scope guardrails plus carry-forward locks:

**From `capabilities.md` cap #5:**
- No DoD-runner that *executes* tests. Cap-5 is shape-only. Whether a `passed` DoD result actually corresponds to green pytest output is reviewer-check material.
- No `gated_on` auto-resolution — `done` updates the state but does not trigger downstream Job promotion. That's cap #10.
- No automatic Learning capture beyond what the agent submits in `learnings[]`. Cap-5 does NOT auto-draft Learnings from run trace.
- No Run Ledger query (just emit the audit row) — `list_runs` / `get_run` ship in cap #7.
- No D&L promotion / dedup / similarity ranking. Cap-5 inserts whatever the agent submits, attached to the submitting Job. Cap #9 ships standalone D&L ops with manual edit / supersede semantics.
- No Contract Profile registry (cap-3.5 / Decision 3 — already removed; AQ2-50 cancelled).

**Cap-5-specific forbids:**
- **No `link_jobs` public op.** Cap-5 inserts ONE `gated_on` edge inline at submit time when `outcome='blocked'`. The general `link_jobs` op (with all 5 edge types when cap #9/#10 add 2 more) is cap #10.
- **No `unlink_jobs`, no `list_job_edges`.** Cap #10.
- **No standalone D&L ops.** `create_decision`, `submit_learning`, `list_decisions`, `list_learnings`, `get_decision`, `get_learning`, `supersede_decision`, `edit_learning` are all cap #9.
- **No inheritance lookups in `get_*` responses.** Cap #9 wires the cap-3 placeholder arrays.
- **No `job_references_decision` / `job_references_learning` edge types.** Cap #9/#10 adds them when standalone D&L cross-references arrive.
- **No Pipeline closure.** Filed as AQ2-73; resolved before cap #6.
- **No `decisions.supersedes_decision_id` write path at submit time.** Field exists in the schema for cap #9's `supersede_decision` op; cap-5 always writes NULL.
- **No conditional ADR-AQ-030 category validation** (`test_report`, `artifacts`, `sbom_and_provenance`, `reproducibility_package`, `failure_taxonomy`, `ui_verification`, `diff_url`, `followup_ticket_id`). Reviewer-check only.
- **No path-finding / multi-hop dependency analysis** (cap-10).
- **No new env vars.** Cap-4 added two; cap-5 adds zero.

---

## Stories (7, each parented to the cap #5 epic)

Each story carries: Objective, Why this matters (human outcome), Scope (in/out), Verification commands, DoD items table, Depends on, Submission shape.

### Story 5.1 — Schema delta + Pydantic models

**Objective:** One Alembic migration `0007_cap05_decisions_and_learnings` creates the `decisions` and `learnings` tables with their indexes per Locked Decision 1. Pydantic request/response models for `submit_job` (discriminated union, 4 variants) and `review_complete`. Inline submission models for `SubmitDecisionInline` and `SubmitLearningInline`. `SubmitJobResponse` with `created_decisions` / `created_learnings` / `created_gated_on_edge` fields per LD 4. `ReviewCompleteRequest` / `ReviewCompleteResponse`. Round-trippable: `alembic upgrade head → downgrade -1 → upgrade head` produces identical schema.

**Why this matters (human outcome):** The schema and contract foundation cap-5 needs exists. The DB has tables to attach Decisions and Learnings to (and the indexes that cap #9's inheritance lookups will use later). The Pydantic models lock the four submission shapes so that later stories layer on the service / route / CLI / MCP wiring without reshaping requests. No runtime behavior changes from this story alone — Story 5.2 brings the first op online.

**Scope (in):**
- `apps/api/alembic/versions/0007_cap05_decisions_and_learnings.py` — single revision creating both tables + 4 indexes (2 per table).
- `apps/api/src/aq_api/models/db.py` — add `Decision` and `Learning` SQLAlchemy ORM models. Mirror existing patterns (`Mapped[...]`, `mapped_column`, `__table_args__` for CHECK + indexes).
- `apps/api/src/aq_api/models/decisions.py` (new) — Pydantic models: `SubmitDecisionInline{title: str, statement: str, rationale: str | None = None}`, `Decision{id, attached_to_kind, attached_to_id, title, statement, rationale, supersedes_decision_id, created_by_actor_id, created_at, deactivated_at}` (the read shape — cap #9 will use this; cap-5 ships it for forward-compat). All `extra='forbid', frozen=True`.
- `apps/api/src/aq_api/models/learnings.py` (new) — same pattern: `SubmitLearningInline{title, statement, context}`, `Learning{...}`.
- `apps/api/src/aq_api/models/jobs.py` (modify) — add the 4 `SubmitJob*Request` variants + the `SubmitJobRequest = Annotated[Union[...], Discriminator('outcome')]` alias + `SubmitJobResponse{job, created_decisions, created_learnings, created_gated_on_edge}` (NO `audit_row_id` field per LD 4 / gate-3 P1-2). Add `ReviewCompleteRequest{final_outcome: Literal['done', 'failed'], notes: str | None = None}` and `ReviewCompleteResponse{job}`.
- `apps/api/src/aq_api/models/__init__.py` — re-exports.
- `apps/api/tests/test_models_cap05.py` (new) — Pydantic round-trip + `extra='forbid'` rejection + discriminator selection tests for each outcome variant.

**Scope (out):**
- No service layer (Story 5.2+).
- No routes, no CLI, no MCP wiring.
- No D&L inline-create logic (Story 5.2 ships it; Story 5.5 covers atomicity).
- No FK to `jobs(id)` from `decisions.attached_to_id` / `learnings.attached_to_id` (polymorphic; application-level enforcement).

**Verification:**
```
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
docker compose exec -T db psql -U aq -d aq2 -c "\d decisions"   # 10 columns + 2 indexes + CHECK + FK to actors
docker compose exec -T db psql -U aq -d aq2 -c "\d learnings"   # 9 columns + 2 indexes + CHECK + FK to actors
docker compose exec -T api uv run alembic -c apps/api/alembic.ini downgrade -1
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head    # idempotent
docker compose exec -T api uv run pytest -q apps/api/tests/test_models_cap05.py
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/models/
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.1-01 | `decisions` table created with all 10 columns + CHECK + FK + 2 indexes per LD 1 | command | `artifacts/cap-05/schema-decisions.txt` | `\d decisions` matches expected output exactly |
| DOD-AQ2-S5.1-02 | `learnings` table created with all 9 columns + CHECK + FK + 2 indexes per LD 1 | command | `artifacts/cap-05/schema-learnings.txt` | `\d learnings` matches expected output exactly |
| DOD-AQ2-S5.1-03 | Migration round-trips (upgrade → downgrade -1 → upgrade) cleanly | command | `artifacts/cap-05/alembic-roundtrip.txt` | both upgrade runs succeed; second upgrade produces no schema change |
| DOD-AQ2-S5.1-04 | `decisions.supersedes_decision_id` is nullable FK to `decisions(id)` with `ON DELETE RESTRICT` | command | same | `\d decisions` shows the FK |
| DOD-AQ2-S5.1-05 | `job_edges.edge_type` CHECK constraint is unchanged (still `('gated_on','parent_of','sequence_next')`) | command | `artifacts/cap-05/job-edges-check-unchanged.txt` | `\d job_edges` shows the same CHECK as cap-3.5 |
| DOD-AQ2-S5.1-06 | All 4 `SubmitJob*Request` variants + `SubmitDecisionInline` + `SubmitLearningInline` + `SubmitJobResponse` + `ReviewComplete*` have `extra='forbid', frozen=True` | grep + test | `artifacts/cap-05/models-shape.txt` | grep count of `extra='forbid'` matches expected (≥9 new occurrences); pytest round-trips each model |
| DOD-AQ2-S5.1-07 | Pydantic discriminator on `outcome` selects the correct variant: `{"outcome":"done", ...}` parses as `SubmitJobDoneRequest`; same for the other 3 outcomes; bad outcome → ValidationError | test | `artifacts/cap-05/discriminator-selection.xml` | pytest covers all 4 valid + 1 invalid case |
| DOD-AQ2-S5.1-08 | mypy `--strict` passes on all new model files | command | `artifacts/cap-05/mypy-cap05-models.txt` | clean |

**Depends on:** Cap-4 schema (jobs has `claim_heartbeat_at`; pipelines has `is_template` + `archived_at`; actors has the system sweeper row; `audit_log` has `error_code` field).

**Submission shape (ADR-AQ-030):** `outcome ∈ {done, failed, blocked}`. `dod_results[]` = 8 entries. `files_changed[]` = the migration + 2 new model files + `db.py` + `jobs.py` + `__init__.py` re-exports. `risks_or_deviations` = `[]` unless something hit. `handoff = "AQ2-S5.2 (Story 5.2 — submit_job(outcome=done) atomic submit service + REST + CLI + MCP, C1 checkpoint)"`. First commit on branch `aq2-cap-05`.

---

### Story 5.2 — `submit_job(outcome='done')` + inline D&L creation + `BusinessRuleException(details=)` extension + REST + CLI + MCP — CHECKPOINT C1

**Implementation note (per gate-3 follow-up):** Story 5.2 is intentionally larger than other cap-5 stories because the foundational `BusinessRuleException(details=)` extension (LD 23) is the FIRST consumer's blocker — splitting them creates an interim where infra ships with no consumer, and the regression coverage can only assert "no break in cap-1/2/3/4 callers" but not "the new `details` field works end-to-end." **Codex MAY sequence implementation as a local sub-flow** (suggested order: 1. extend `BusinessRuleException` + route serialization + cap-1/2/3/4 regression test [proves backward-compat]; 2. ship `_contract_validator.py` raising `details`-bearing exceptions; 3. ship `submit.py` service + REST route; 4. wire CLI + MCP; 5. inline D&L helper; 6. parity test regen). **The final story commit is ONE squashed commit at the C1 boundary** — preserves cap-4's "one commit per story" discipline (no per-step commits on the cap-5 branch). Codex may push WIP commits to a private working branch during implementation, but the cap-5 history records exactly one Story 5.2 commit. If at any local checkpoint the work stalls or a foundational decision needs revisiting, Codex pauses and pings Mario; otherwise the squash-to-one-commit happens at the end.

**Objective:** Ship the canonical `submit_job` op for the `outcome='done'` path across all three surfaces, **including inline Decision/Learning creation per LD 13** (per gate-3 P1-2: original split silently dropped non-empty D&L until Story 5.5; now folded forward into 5.2). Extend `BusinessRuleException` and route error serialization to support `details: Mapping[str, object] | None` per LD 23. Service in `apps/api/src/aq_api/services/submit.py`; route `POST /jobs/{id}/submit`; CLI `aq job submit <job-id> --outcome done --payload @file.json`; MCP tool `submit_job(job_id, payload)` with `destructiveHint=true`. Atomic single-transaction semantics: `audited_op` wraps SELECT-FOR-UPDATE Job fetch, validation, state UPDATE (with claim-field clearing per LD 7), N decision INSERTs, M learning INSERTs, and audit-row insert. **Done-path contract validation per LD 5.** Multi-part MCP response: Job + text block. Parity tests regenerated for `submit_job`. **C1 checkpoint fires here.**

**Why this matters (human outcome):** This is the cap. After Story 5.2 lands, an agent who has claimed a Job (cap-4) can call `aq job submit <job-id> --outcome done --payload @closeout.json` (with `decisions_made[]` and `learnings[]` arrays in the payload) and finish the work. The Job transitions to `done`; claim fields clear; the audit log records who submitted what; D&L rows are inserted attached to the Job; the loop closes for the success path. Race conditions with the auto-release sweep are covered in Story 5.6, but the single-actor happy path + every contract-violation rejection + inline D&L creation are covered here. C1 is the natural mid-capability stop because the loop is fully exercisable for the most-tested outcome.

**Scope (in):**
- `apps/api/src/aq_api/_audit.py` (modify) — extend `BusinessRuleException.__init__` to accept `details: Mapping[str, object] | None = None` (default None preserves cap-1/2/3/4 caller behavior). Update `audited_op` denial path to merge `exc.details` into `response_payload` per LD 23. Default behavior unchanged when `details is None`.
- `apps/api/src/aq_api/routes/_errors.py` (or wherever the BusinessRuleException handler lives — locate in Story 5.2 first commit) — extend the JSON response body to include `details` ONLY when `exc.details` is set; absent `details` preserves the existing `{"error": exc.error_code}` shape.
- `apps/api/src/aq_api/services/submit.py` (new) — `submit_job(session, *, job_id, request, actor_id) -> SubmitJobResponse`. Uses `audited_op`. Validation order per LD 5: SELECT FOR UPDATE → existence (404) → state (409) → auth (403) → contract shape (422 `contract_violation` with `details`). UPDATE: `state='done'`, `claimed_by_actor_id=NULL`, `claimed_at=NULL`, `claim_heartbeat_at=NULL` (per LD 7). Inline D&L creation per LD 13: for each entry in `request.decisions_made[]` insert one row into `decisions` (`attached_to_kind='job', attached_to_id=job_id, ...`); for each entry in `request.learnings[]` insert one row into `learnings`. Collect returned IDs into `created_decisions: list[UUID]` and `created_learnings: list[UUID]`. Helper extracted into `_insert_inline_dl(session, *, job_id, actor_id, decisions_made, learnings) -> tuple[list[UUID], list[UUID]]` for testability + reuse by Story 5.3's other outcomes. Audit `response_payload`: `{outcome: 'done', created_decisions: [str(uid) for uid in d_ids], created_learnings: [...], created_gated_on_edge: false}`. Return `SubmitJobResponse(job=updated_job, created_decisions=d_ids, created_learnings=l_ids, created_gated_on_edge=False)` (per LD 4 — no `audit_row_id` field).
- `apps/api/src/aq_api/services/_contract_validator.py` (new) — `validate_contract_shape_for_outcome(contract: dict, request, outcome: str) -> None`. Pure function, no DB. Raises `BusinessRuleException(422, 'contract_violation', ..., details={'rule': '<sub-code>', 'dod_id': '...', 'expected': '...', 'got': '...'})` per LD 5 per-outcome rules. Tested directly + via service tests.
- `apps/api/src/aq_api/routes/jobs.py` (modify) — add `POST /jobs/{id}/submit` route. Body: `SubmitJobRequest` (discriminated union — FastAPI/Pydantic auto-routes by `outcome` field). Response: `SubmitJobResponse`.
- `apps/cli/src/aq_cli/main.py` (modify) — add `aq job submit <job-id> --outcome <enum> --payload @<file>` Typer command (Story 5.2 wires it for `done` only; failed/blocked/pending_review extend in Story 5.3). Reads `--payload` from a JSON file or `-` for stdin.
- `apps/api/src/aq_api/mcp.py` (modify) — register `submit_job` tool with `annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}`. Multi-part output: list of FastMCP `Content` blocks (Job JSON + text block per LD 18; **no audit-row reference content block** per LD 4 / gate-3 P1-2).
- `apps/api/tests/test_submit_job_done.py` (new) — happy path (no D&L), happy path (with N=2 decisions + M=3 learnings; assert created_decisions/created_learnings IDs match DB rows), contract-violation rejection (every variant: missing dod_id, duplicate dod_id, bad status enum, missing required DoD under `done`, `failed`/`blocked` status under `done`, missing evidence under `passed`), wrong claimant 403, terminal-state 409, not-found 404, audit row shape (success + denial), claim fields cleared on success (assert `claimed_by_actor_id IS NULL` post-submit). Use `_isolated_schema.py` for any tests touching `audit_log` or D&L tables.
- `apps/api/tests/test_business_rule_exception_details.py` (new) — regression: every existing cap-1/2/3/4 denial still returns the same JSON shape (`{"error": "..."}` only, no `details` key) when `details` is not constructed. Sample: cap-2 `revoke_api_key` cross-actor 403, cap-3 `update_job` reject-state 400, cap-4 `release_job` wrong-claimant 403 — assert byte-equal to pre-cap-5 shape.
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated to include `submit_job` schema (review_complete adds in Story 5.4).
- `tests/parity/test_four_surface_parity.py` — add `-k submit` parametrized case asserting REST + CLI + MCP byte-equal payloads (Web skipped per cap-3 precedent).

**Scope (out):**
- No `failed`/`blocked`/`pending_review` outcome wiring (Story 5.3 — but the D&L helper + validation framework are in place; Story 5.3 is mostly outcome-specific request models + per-outcome validation rule sets).
- No `review_complete` (Story 5.4).
- No D&L atomicity tests with failure injection (Story 5.5 — feature is shipped here; failure-injection coverage lives there).
- No race tests (Story 5.6).
- No `capabilities.md` fix-up (Story 5.7).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_submit_job_done.py apps/api/tests/test_business_rule_exception_details.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k submit
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/submit.py apps/api/src/aq_api/services/_contract_validator.py apps/api/src/aq_api/_audit.py
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests   # full regression
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.2-01 | `submit_job(outcome=done)` succeeds on a claimed `in_progress` Job: state→`done`, success audit row written with `error_code=NULL` and `response_payload.outcome='done'` | test | `artifacts/cap-05/submit-done-success.xml` | live test asserts state + audit |
| DOD-AQ2-S5.2-02 | Audit `request_payload` carries the full submission (post-redaction); `target_kind='job'`, `target_id=job_id` | test | same | live test asserts shape |
| DOD-AQ2-S5.2-03 | Claim fields are cleared on successful `done` submit: `claimed_by_actor_id IS NULL`, `claimed_at IS NULL`, `claim_heartbeat_at IS NULL` per LD 7 | test | `artifacts/cap-05/submit-done-claim-cleared.xml` | live SQL assertion post-submit |
| DOD-AQ2-S5.2-04 | Inline D&L creation: submit with N=2 decisions + M=3 learnings → `created_decisions` has 2 IDs + `created_learnings` has 3 IDs in response; corresponding rows exist in `decisions` / `learnings` tables with `attached_to_kind='job', attached_to_id=submitting_job_id` | test | `artifacts/cap-05/submit-done-inline-dl.xml` | live test asserts response IDs match DB rows |
| DOD-AQ2-S5.2-05 | Empty `decisions_made[]`/`learnings[]` → `created_decisions=[]`, `created_learnings=[]`; no rows in either table for this Job | test | same | live test |
| DOD-AQ2-S5.2-06 | Contract validator: `dod_results[].dod_id` not in `contract.dod_items[].id` → 422 `contract_violation` with `details = {'rule': 'dod_id_unknown', 'dod_id': '<offending>'}` | test | `artifacts/cap-05/contract-violation-cases.xml` | live test asserts response body and audit row's response_payload both carry the details |
| DOD-AQ2-S5.2-07 | Contract validator: duplicate `dod_id` in `dod_results` → 422 `contract_violation` with `details.rule='duplicate_dod_id'` | test | same | live test |
| DOD-AQ2-S5.2-08 | Contract validator: `outcome=done` with any `dod_result.status ∈ {failed, blocked}` → 422 `contract_violation` with `details.rule='incomplete_dod'` | test | same | live test |
| DOD-AQ2-S5.2-09 | Contract validator: `outcome=done` missing required `dod_id` from `contract.dod_items[]` → 422 `contract_violation` with `details.rule='missing_required_dod'` and `details.dod_id` naming the missing one | test | same | live test |
| DOD-AQ2-S5.2-10 | Contract validator: `passed` result with empty `evidence[]` → 422 `contract_violation` with `details.rule='no_evidence'` | test | same | live test |
| DOD-AQ2-S5.2-11 | Pydantic-level rejection (missing field, `extra='forbid'`, bad status enum) → 422, NOT audited | test | `artifacts/cap-05/submit-pydantic-rejections.xml` | `audit_log` count delta = 0 across N invalid requests |
| DOD-AQ2-S5.2-12 | Validation order per LD 5: 404 → 409 → 403 → 422 — terminal Job (state=done) by wrong actor returns 409 NOT 403 | test | `artifacts/cap-05/validation-order.xml` | matrix test covers ordering |
| DOD-AQ2-S5.2-13 | Job not found → 404 `job_not_found` with audit row | test | `artifacts/cap-05/submit-denials.xml` | live test |
| DOD-AQ2-S5.2-14 | Wrong claimant on `in_progress` Job → 403 `submit_forbidden` with audit row | test | same | live test |
| DOD-AQ2-S5.2-15 | State ≠ `in_progress` (terminal `done`/`failed`/`cancelled` OR non-claimed `ready`/`pending_review`/`blocked`) → 409 `job_not_in_progress` with audit row | test | same | live test covers all 6 non-`in_progress` cases |
| DOD-AQ2-S5.2-16 | `BusinessRuleException(details=...)` extension: cap-1/2/3/4 denial paths still return identical JSON shape (no `details` key when not set) — regression test on `revoke_api_key` cross-actor 403, `update_job` reject-state 400, `release_job` wrong-claimant 403 | test | `artifacts/cap-05/exception-details-regression.xml` | byte-equal pre-cap-5 shape |
| DOD-AQ2-S5.2-17 | MCP `submit_job` returns multi-part content list (Job + text block) per LD 18 | test | `artifacts/cap-05/mcp-submit-multipart.xml` | live MCP client call asserts 2-block response |
| DOD-AQ2-S5.2-18 | Parity test green for `submit_job(outcome=done)` across REST + CLI + MCP | test | `artifacts/cap-05/parity-submit-done.xml` | byte-equal payloads |

**Depends on:** Story 5.1.

**Submission shape (ADR-AQ-030):** Second commit. `dod_results[]` = 18 entries. `handoff = "AQ2-S5.3 (Story 5.3 — submit_job(outcome=pending_review|failed|blocked) + gated_on edge)"`. **CHECKPOINT C1 fires here** — Codex stops, posts evidence on cap-5 epic, awaits Ghost approval before Story 5.3.

---

### Story 5.3 — `submit_job` other three outcomes (`pending_review`, `failed`, `blocked`) + `gated_on` edge insertion

**Objective:** Layer the remaining three outcomes onto the Story 5.2 service skeleton. The D&L inline-creation helper from Story 5.2 is reused for all three outcomes (each accepts `decisions_made[]` and `learnings[]`). `outcome='pending_review'`: state → `pending_review` + claim-fields-cleared (LD 7); contract validation requires dod_id match but allows non-terminal statuses (per LD 5). `outcome='failed'`: state → `failed`; `failure_reason` required by Pydantic; `dod_results[]` MAY be empty (LD 5 `failed` rules — partial reporting allowed; if non-empty, dod_id match still required). `outcome='blocked'`: state → `blocked`; `gated_on_job_id` field required; **`SubmitJobBlockedRequest` does NOT have a `dod_results` field** (Pydantic `extra='forbid'` rejects it per gate-3 P1-4). Service inserts one `job_edges(from=submitter, to=gated_on_job_id, edge_type='gated_on')` row inline; validates target Job exists, same project, not self. CLI/MCP/REST wire all three. Parity tests regenerated for the additional outcomes.

**Why this matters (human outcome):** With Story 5.3 done, all four `submit_job` outcomes work end-to-end. An agent that hits a wall can submit `outcome=failed` with a reason and move on. An agent that needs human review can submit `outcome=pending_review`. An agent that's blocked on another Job can submit `outcome=blocked` with a `gated_on_job_id`, AQ persists the dependency edge, and cap #10 will later auto-promote the blocked Job back to `ready` when the gating Job completes.

**Scope (in):**
- `apps/api/src/aq_api/services/submit.py` (modify) — extend the `audited_op` block:
  - Branch on `request.outcome`. For `pending_review`/`failed`: same existence/state/auth checks (LD 5 order); per-outcome contract validator dispatch (LD 5 rules per outcome); state UPDATE with claim-field clearing (LD 7); reuse Story 5.2's `_insert_inline_dl` helper for D&L creation. For `blocked`: same existence/state/auth checks; **NO contract validation** (model excludes `dod_results`); fetch the gating Job; assert exists (else `BusinessRuleException(409, 'gated_on_invalid', details={'rule': 'not_found'})`) + same `project_id` (else `details={'rule': 'cross_project'}`) + `id != submitting_job_id` (else `details={'rule': 'self'}`). Insert the edge: `INSERT INTO job_edges (from_job_id, to_job_id, edge_type) VALUES (:s, :g, 'gated_on')`. Catch SQLAlchemy `IntegrityError` (PK collision = duplicate edge), invoke `await session.rollback()` (the IntegrityError poisons the transaction; rollback before raising the BusinessRuleException so `audited_op`'s denial path can still write the audit row), then raise `BusinessRuleException(409, 'gated_on_already_exists', details={'gated_on_job_id': str(gated_on_job_id)})`. Reuse `_insert_inline_dl` after the edge insert (failure of D&L inserts also rolls back the edge per LD 20).
- `apps/api/src/aq_api/services/_contract_validator.py` (modify) — `validate_contract_shape_for_outcome(contract, request, outcome)` dispatches per LD 5 rules: `done` rules already shipped in 5.2; ADD `pending_review` (dod_id match required; any of 4 statuses; passed needs evidence; no duplicates), `failed` (empty `dod_results` accepted; non-empty requires dod_id match + status enum + no duplicates; evidence not enforced), `blocked` (no validation — Pydantic already rejected `dod_results` if supplied).
- `apps/cli/src/aq_cli/main.py` (modify) — `aq job submit` already exists from 5.2; ensure it routes all four `--outcome` values.
- `apps/api/src/aq_api/mcp.py` (modify) — `submit_job` tool already registered from 5.2; add per-outcome description text + an example for each.
- `apps/api/tests/test_submit_job_pending_review.py`, `test_submit_job_failed.py`, `test_submit_job_blocked.py` (3 new files) — happy path (with + without D&L) + per-outcome validation rejections + claim-field-clearing assertions + gated_on edge insertion (visible in `job_edges`) + cross-project/self/not-found rejections.
- `apps/api/tests/test_submit_job_blocked_edge_atomicity.py` (new, per gate-3 P2-1) — duplicate-edge collision via pre-seeded fixture. Setup: insert `job_edges(from_job_id=A, to_job_id=B, edge_type='gated_on')` directly via test fixture INSERT (NOT via `submit_job` flow — that path is impossible since reset_claim doesn't work on `blocked` Jobs). Then claim A (via cap-4 `claim_next_job`) so it's `in_progress`, then `submit_job(outcome='blocked', gated_on_job_id=B)` → expect `BusinessRuleException(409, 'gated_on_already_exists', details={'gated_on_job_id': str(B)})`. Assert: edge row count unchanged (still 1), Job A still `in_progress` (rollback happened cleanly), one denial audit row.
- `apps/api/tests/test_submit_job_blocked_pydantic_excludes_dod_results.py` (new) — assert `SubmitJobBlockedRequest` rejects `dod_results` field via `extra='forbid'`. Submit POST body `{"outcome": "blocked", "gated_on_job_id": "...", "dod_results": []}` → 422 (Pydantic), NOT audited.
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated for the 3 additional discriminator variants.
- `tests/parity/test_four_surface_parity.py` — add `-k "submit_pending_review or submit_failed or submit_blocked"` cases.

**Scope (out):**
- No `review_complete` (Story 5.4).
- D&L inline-creation feature is shipped in 5.2 + reused here; **only failure-injection atomicity tests** live in Story 5.5.
- No 50-claimant submit race or sweep-vs-submit race tests (Story 5.6).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_submit_job_pending_review.py apps/api/tests/test_submit_job_failed.py apps/api/tests/test_submit_job_blocked.py apps/api/tests/test_submit_job_blocked_edge_atomicity.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k "submit_pending_review or submit_failed or submit_blocked"
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/submit.py apps/api/src/aq_api/services/_contract_validator.py
docker compose exec -T api uv run ruff check apps/api apps/cli
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.3-01 | `submit_job(outcome=pending_review)`: state→`pending_review`, claim fields cleared (LD 7), success audit, `response_payload.outcome='pending_review'` | test | `artifacts/cap-05/submit-pending-review.xml` | live test asserts state + cleared claim fields + audit |
| DOD-AQ2-S5.3-02 | `submit_job(outcome=failed)`: state→`failed`, claim fields cleared, success audit, `failure_reason` recorded in `request_payload`, `error_code=NULL` (per LD 6) | test | `artifacts/cap-05/submit-failed.xml` | live test asserts `error_code IS NULL` and cleared claim fields |
| DOD-AQ2-S5.3-03 | `submit_job(outcome=blocked)`: state→`blocked`, claim fields cleared, success audit, `created_gated_on_edge=true` in response, `job_edges` row inserted | test | `artifacts/cap-05/submit-blocked.xml` | live test asserts edge presence + cleared claim fields |
| DOD-AQ2-S5.3-04 | `pending_review` outcome accepts `dod_results` with non-terminal statuses; dod_id match still required per LD 5 | test | same | live test passes a `dod_results` with mixed statuses ALL with dod_ids matching contract |
| DOD-AQ2-S5.3-05 | `pending_review` outcome rejects unknown `dod_id` with 422 `contract_violation:dod_id_unknown` (proves dod_id match still applies) | test | `artifacts/cap-05/submit-pending-review-validation.xml` | live test |
| DOD-AQ2-S5.3-06 | `failed` outcome accepts empty `dod_results[]` | test | `artifacts/cap-05/submit-failed.xml` | live test asserts state→`failed` with `dod_results=[]` |
| DOD-AQ2-S5.3-07 | `failed` outcome with non-empty `dod_results` still requires dod_id match (per LD 5) | test | `artifacts/cap-05/submit-failed-with-results.xml` | live test: failed with bad dod_id → 422 `contract_violation:dod_id_unknown` |
| DOD-AQ2-S5.3-08 | `SubmitJobBlockedRequest` rejects `dod_results` field via Pydantic `extra='forbid'` → 422, NOT audited | test | `artifacts/cap-05/submit-blocked-pydantic.xml` | `audit_log` count delta = 0 |
| DOD-AQ2-S5.3-09 | `gated_on_job_id` not found → 409 `gated_on_invalid` with `details.rule='not_found'`, audit row | test | `artifacts/cap-05/submit-blocked-denials.xml` | live test |
| DOD-AQ2-S5.3-10 | `gated_on_job_id` cross-project → 409 `gated_on_invalid` with `details.rule='cross_project'`, audit row | test | same | live test |
| DOD-AQ2-S5.3-11 | `gated_on_job_id == submitting_job_id` → 409 `gated_on_invalid` with `details.rule='self'`, audit row | test | same | live test |
| DOD-AQ2-S5.3-12 | Duplicate `gated_on` edge via pre-seeded fixture: pre-INSERT `job_edges(A, B, 'gated_on')`, claim A, submit blocked with `gated_on_job_id=B` → 409 `gated_on_already_exists` with `details.gated_on_job_id=B`; original edge row count unchanged (still 1); Job A still `in_progress` (rollback clean); one denial audit row | test | `artifacts/cap-05/submit-blocked-edge-collision.xml` | live test (per gate-3 P2-1 — pre-seeded, NOT reset_claim flow) |
| DOD-AQ2-S5.3-13 | Atomicity: gated-on edge insertion failure (e.g., target Job hard-deleted between SELECT and INSERT — FK violation injection) rolls back state transition AND audit row + AND any inline D&L inserts; no partial state | test | same | live test asserts state still `in_progress`, no audit row, no edge, no D&L rows |
| DOD-AQ2-S5.3-14 | Inline D&L creation works for all 3 outcomes (pending_review, failed, blocked): N=2 + M=2 D&L rows attached to submitting Job, response carries IDs | test | `artifacts/cap-05/submit-other-outcomes-inline-dl.xml` | live test for each of the 3 outcomes |
| DOD-AQ2-S5.3-15 | Parity test green for all 3 new outcomes across REST + CLI + MCP | test | `artifacts/cap-05/parity-submit-other-outcomes.xml` | byte-equal payloads |

**Depends on:** Story 5.2.

**Submission shape (ADR-AQ-030):** Third commit. `dod_results[]` = 15 entries. `handoff = "AQ2-S5.4 (Story 5.4 — review_complete + state-machine completeness tests)"`.

---

### Story 5.4 — `review_complete` + state-machine completeness

**Objective:** Implement `review_complete(session, *, job_id, request, actor_id) -> ReviewCompleteResponse` in `apps/api/src/aq_api/services/review.py`. Any-actor (no claimant check; mirrors `reset_claim` cap-4 pattern). Valid only when `state='pending_review'`. `final_outcome ∈ {done, failed}` enforced at Pydantic boundary. Wire REST `POST /jobs/{id}/review-complete`, CLI `aq job review-complete <job-id> --final-outcome <enum> [--notes "..."]`, MCP `review_complete`. Plus a comprehensive state-machine completeness test that exercises every legal and illegal transition for cap-5 ops against every state in the 8-state CHECK enum.

**Why this matters (human outcome):** A `pending_review` Job no longer gets stuck. Any actor (typically Mario or a reviewing agent) can call `aq job review-complete <job-id> --final-outcome done` to terminate it. The audit row records the reviewing actor (who may be different from the original claimant), preserving the forensic trail of "who said this work was good." The state-machine test pins down every transition cap-5 introduces — illegal ones (e.g., submit on `done`, review_complete on `ready`) all fail with the locked error codes.

**Scope (in):**
- `apps/api/src/aq_api/services/review.py` (new) — `review_complete(session, *, job_id, request, actor_id)`. Uses `audited_op` per the standard pattern. SELECT FOR UPDATE. 404 → `job_not_found`. State check: `db_job.state != 'pending_review'` → 409 `job_not_pending_review`. UPDATE state to `request.final_outcome`. Audit `response_payload = {final_outcome, prior_state: 'pending_review'}`. Returns `ReviewCompleteResponse(job=updated_job)`.
- `apps/api/src/aq_api/routes/jobs.py` (modify) — add `POST /jobs/{id}/review-complete`.
- `apps/cli/src/aq_cli/main.py` (modify) — add `aq job review-complete` command.
- `apps/api/src/aq_api/mcp.py` (modify) — register `review_complete` tool with `destructiveHint=true`. Description: "Resolve a `pending_review` Job to a terminal state. Any actor with a valid key may call this; the reviewing actor is recorded in the audit log. final_outcome must be `done` or `failed` — pending_review and blocked are not valid second-pass outcomes."
- `apps/api/tests/test_review_complete.py` (new) — happy path (`done` and `failed`), wrong state (`ready`/`in_progress`/`done`/`failed`/`blocked`/`cancelled`/`draft`) → 409, not-found 404, Pydantic rejection of `final_outcome ∈ {pending_review, blocked, draft, ready, in_progress, cancelled}`, audit row records the reviewing actor's `authenticated_actor_id` (NOT the original claimant's), reviewing actor differs from claimant case.
- `apps/api/tests/test_state_machine_completeness_cap05.py` (new) — matrix test: for every (state, op) pair in `{8 states} × {submit_job, review_complete}`, assert the expected outcome (allowed transition / specific error code). Also asserts: `cancel_job` still works on `pending_review` and `blocked` (per LD 7); `claim_next_job` does NOT see a `pending_review`/`blocked`/`done`/`failed`/`cancelled` Job.
- `tests/parity/openapi.snapshot.json` + `tests/parity/mcp_schema.snapshot.json` — regenerated to add `review_complete` schema.
- `tests/parity/test_four_surface_parity.py` — add `-k review_complete`.

**Scope (out):**
- No D&L inline creation (Story 5.5).
- No race / atomicity tests (Story 5.6).
- No `cancel_job` re-implementation (cap-3 already ships it).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_review_complete.py apps/api/tests/test_state_machine_completeness_cap05.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py -k review_complete
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/services/review.py
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests   # full regression
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.4-01 | `review_complete(final_outcome=done)`: state→`done`, success audit (error_code=NULL), `authenticated_actor_id`=reviewer (NOT original claimant) | test | `artifacts/cap-05/review-complete-done.xml` | live test |
| DOD-AQ2-S5.4-02 | `review_complete(final_outcome=failed)`: state→`failed`, success audit | test | `artifacts/cap-05/review-complete-failed.xml` | live test |
| DOD-AQ2-S5.4-03 | Reviewer can be ANY valid Bearer key (not necessarily the original claimant) | test | same | test claims as actor A, submits pending_review as A, calls review_complete as actor B; succeeds; audit row's `authenticated_actor_id` = B |
| DOD-AQ2-S5.4-04 | State ≠ `pending_review` → 409 `job_not_pending_review` with audit row | test | `artifacts/cap-05/review-complete-denials.xml` | live test covers all 7 non-`pending_review` states |
| DOD-AQ2-S5.4-05 | Job not found → 404 `job_not_found` with audit row | test | same | live test |
| DOD-AQ2-S5.4-06 | `final_outcome` not in `{done, failed}` → 422 (Pydantic), NOT audited | test | `artifacts/cap-05/review-complete-pydantic.xml` | `audit_log` count delta = 0 across 6 invalid `final_outcome` values |
| DOD-AQ2-S5.4-07 | Cap-5 state-machine matrix: `submit_job` valid only from `in_progress`; `review_complete` valid only from `pending_review`; both deny ALL other states with the locked error code | test | `artifacts/cap-05/state-machine-matrix.xml` | matrix test runs all 16 (state × op) cases |
| DOD-AQ2-S5.4-08 | `cancel_job` still works on `pending_review` and `blocked` (per LD 7); explicit test pins this as deliberate | test | `artifacts/cap-05/cancel-on-pending-review.xml` | live test cancels a `pending_review` Job; state→`cancelled`; audit row written |
| DOD-AQ2-S5.4-09 | `claim_next_job` does NOT return `pending_review`/`blocked`/`done`/`failed`/`cancelled` Jobs (only `ready`) | test | `artifacts/cap-05/claim-skips-non-ready.xml` | seeded fixtures with one Job per state; claim returns only the `ready` Job |
| DOD-AQ2-S5.4-10 | Parity test green for `review_complete` across REST + CLI + MCP | test | `artifacts/cap-05/parity-review-complete.xml` | byte-equal payloads |

**Depends on:** Story 5.3.

**Submission shape (ADR-AQ-030):** Fourth commit. `dod_results[]` = 10 entries. `handoff = "AQ2-S5.5 (Story 5.5 — D&L atomicity tests + EXPLAIN evidence + scale tests)"`.

---

### Story 5.5 — D&L atomicity tests + EXPLAIN evidence + scale tests

**Objective:** Pure test coverage. The D&L inline-creation feature was shipped in Story 5.2 + reused in Story 5.3 per gate-3 P2-2. Story 5.5 contains rigorous atomicity coverage: failure-injection tests across 3 distinct flush points, multi-row-batch coverage (N=10 decisions + M=10 learnings), interaction tests with `review_complete`, and the EXPLAIN evidence for the LD 17 query shapes. **No new feature code in this story** — all changes are in `apps/api/tests/` and `plans/v2-rebuild/artifacts/cap-05/`.

**Why this matters (human outcome):** The D&L feature shipped in Story 5.2/5.3 needs proof that it's atomic under every reasonable failure mode. Story 5.5's tests pin the invariant: "job state transition + N decision inserts + M learning inserts + (optional) gated_on edge insert + 1 audit row → all commit, or all roll back." Without these tests, a future refactor could silently break the invariant (e.g., a developer moves the audit row write outside the `audited_op` block) and the regression wouldn't be caught.

**Scope (in):**
- `apps/api/tests/test_submit_inline_dl_happy.py` (new) — extends Story 5.2's happy-path coverage: empty arrays (0 D&L); large batches (N=10 decisions + M=10 learnings → exactly 10+10 rows; ALL `attached_to_kind='job', attached_to_id=submitting_job_id`); content fields exact-match (title/statement/rationale/context preserved byte-equal from request to row); `created_at` is server-side `now()` (within 5s of submit), NOT client-supplied; `created_by_actor_id` = the submitting actor; `supersedes_decision_id IS NULL` for every cap-5-created decision.
- `apps/api/tests/test_submit_inline_dl_atomicity.py` (new) — five monkeypatch scenarios using `_isolated_schema.py`:
  1. Failure injected after the state UPDATE but before any D&L insert → no D&L rows, state still `in_progress`, claim fields NOT cleared, no audit row.
  2. Failure injected after the FIRST decision insert (3 decisions, 2 learnings in payload) → no D&L rows ANYWHERE (including the one decision that was almost-flushed), state still `in_progress`, no audit row.
  3. Failure injected between the LAST learning insert and the audit-row write → no D&L rows, state still `in_progress`, no audit row.
  4. (`outcome=blocked`) Failure injected after gated_on edge insert but before D&L: no edge, no D&L, state still `in_progress`, no audit row.
  5. (`outcome=blocked`) Failure injected after D&L inserts but before audit row: no edge, no D&L, state still `in_progress`, no audit row.
- `apps/api/tests/test_submit_inline_dl_review_complete_interaction.py` (new) — submit `outcome=pending_review` with non-empty `decisions_made[]`/`learnings[]`; D&L rows are created at the pending_review submit (NOT deferred to `review_complete`). Subsequent `review_complete(final_outcome=done)` does NOT create additional D&L rows (its request shape excludes them; `extra='forbid'` rejects them at Pydantic level if attempted).
- `apps/api/tests/test_submit_dl_per_attached_kind.py` (new) — schema sanity: cap-5 ONLY writes `attached_to_kind='job'`. Direct DB INSERT with `'pipeline'` and `'project'` succeeds (schema accepts all 3 — cap-9 forward-compat). Submit_job NEVER writes anything except `'job'` (assert via grep on the service code + a behavioral test that no submit can produce `attached_to_kind != 'job'`).
- EXPLAIN evidence (new artifacts): `artifacts/cap-05/explain-decisions-attached-lookup.txt` for `SELECT id, title, statement FROM decisions WHERE attached_to_kind='job' AND attached_to_id=:job_id ORDER BY created_at` — confirm `idx_decisions_attached` is used. Same for `learnings` → `artifacts/cap-05/explain-learnings-attached-lookup.txt`.

**Scope (out):**
- No `get_job` inheritance lookup wiring (cap #9).
- No standalone `create_decision` / `submit_learning` ops (cap #9).
- No `supersedes_decision_id` write path (cap #9 owns `supersede_decision`).
- No 50-claimant race / sweep race (Story 5.6).
- No new feature code (this is a pure test/evidence story).

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests/test_submit_inline_dl_happy.py apps/api/tests/test_submit_inline_dl_atomicity.py apps/api/tests/test_submit_inline_dl_review_complete_interaction.py apps/api/tests/test_submit_dl_per_attached_kind.py
docker compose exec -T api uv run ruff check apps/api
docker compose exec -T db psql -U aq -d aq2 -c "EXPLAIN (ANALYZE, BUFFERS) SELECT id, title, statement FROM decisions WHERE attached_to_kind='job' AND attached_to_id='<seeded-job-id>' ORDER BY created_at"
docker compose exec -T db psql -U aq -d aq2 -c "EXPLAIN (ANALYZE, BUFFERS) SELECT id, title, statement FROM learnings WHERE attached_to_kind='job' AND attached_to_id='<seeded-job-id>' ORDER BY created_at"
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.5-01 | N=10 decisions + M=10 learnings → exactly 10+10 rows; all `attached_to_kind='job'`, `attached_to_id=submitter`; response IDs match DB rows; content fields preserved byte-equal | test | `artifacts/cap-05/submit-dl-large-batch.xml` | live test |
| DOD-AQ2-S5.5-02 | `created_by_actor_id` on every D&L row equals the submitting actor's UUID | test | same | live test |
| DOD-AQ2-S5.5-03 | `created_at` on every D&L row is server-side `now()` (within 5s of submit), NOT client-supplied | test | same | live test |
| DOD-AQ2-S5.5-04 | `supersedes_decision_id IS NULL` on every cap-5-created decision (cap #9 owns supersede) | test | same | live test |
| DOD-AQ2-S5.5-05 | Atomicity scenario 1: failure after state UPDATE before D&L insert → no D&L rows, state still `in_progress`, claim fields NOT cleared, no audit row | test | `artifacts/cap-05/submit-dl-atomicity-1.xml` | monkeypatch test |
| DOD-AQ2-S5.5-06 | Atomicity scenario 2: failure mid-D&L-batch (after 1st decision of 3) → no D&L rows anywhere, state in_progress, no audit | test | `artifacts/cap-05/submit-dl-atomicity-2.xml` | monkeypatch test |
| DOD-AQ2-S5.5-07 | Atomicity scenario 3: failure after last D&L insert before audit-row write → no D&L, state in_progress, no audit | test | `artifacts/cap-05/submit-dl-atomicity-3.xml` | monkeypatch test |
| DOD-AQ2-S5.5-08 | Atomicity scenario 4 (`outcome=blocked`): failure after gated_on edge insert but before D&L → no edge, no D&L, state in_progress, no audit | test | `artifacts/cap-05/submit-dl-atomicity-4.xml` | monkeypatch test |
| DOD-AQ2-S5.5-09 | Atomicity scenario 5 (`outcome=blocked`): failure after D&L inserts before audit row → no edge, no D&L, state in_progress, no audit | test | `artifacts/cap-05/submit-dl-atomicity-5.xml` | monkeypatch test |
| DOD-AQ2-S5.5-10 | `pending_review` submission with non-empty D&L: rows are created at submit time | test | `artifacts/cap-05/submit-dl-pending-review.xml` | live test |
| DOD-AQ2-S5.5-11 | `review_complete` does NOT create additional D&L rows (`ReviewCompleteRequest` excludes them; Pydantic rejects); row count delta = 0 across review_complete | test | same | live test |
| DOD-AQ2-S5.5-12 | Schema accepts all three `attached_to_kind` values (`job`, `pipeline`, `project`) — direct DB INSERT works for each (cap-9 forward-compat); but `submit_job` ONLY ever writes `'job'` (grep `services/submit.py` for `attached_to_kind=` returns only `'job'` constants) | test + grep | `artifacts/cap-05/submit-dl-attached-kind.xml` + `artifacts/cap-05/submit-dl-grep.txt` | both tests pass |
| DOD-AQ2-S5.5-13 | EXPLAIN of D&L attached-lookup query uses `idx_decisions_attached` / `idx_learnings_attached` | command | `artifacts/cap-05/explain-decisions-attached-lookup.txt`, `explain-learnings-attached-lookup.txt` | EXPLAIN plans show the expected index access |

**Depends on:** Stories 5.1, 5.2, 5.3, 5.4.

**Submission shape (ADR-AQ-030):** Fifth commit. `dod_results[]` = 13 entries. `risks_or_deviations` = `[]` unless something hit. `handoff = "AQ2-S5.6 (Story 5.6 — MCP richness + concurrent submit race + sweep-vs-submit race)"`.

---

### Story 5.6 — MCP richness refinement + concurrent submit race + sweep-vs-submit atomicity

**Objective:** Refine the cap-4 MCP richness pattern for cap-5's two new ops. Update the FastMCP server-instructions block to reference `submit_job` / `review_complete` as the canonical exit paths (replacing cap-4's "submit_job ships in cap #5" placeholder text). Verify all 4 cap-4 + 2 cap-5 mutation tools have the right annotations. Ship the concurrent-submit race test (50 simultaneous submit attempts against the same `in_progress` Job → exactly 1 winner, 49 see `403 submit_forbidden` or `409 job_not_in_progress`). Ship the sweep-vs-submit atomicity test (auto-release sweep fires while submit is mid-tx → submit returns `409 job_not_in_progress` with zero partial state). FastMCP version-pin smoke test (5-line guard from LD 22).

**Why this matters (human outcome):** Cap-5's race surface is different from cap-4's. Cap-4 proved 50 concurrent **claimers** produce one winner. Cap-5 proves 50 concurrent **submits against one already-claimed Job** also produce one winner — only the legitimate claimant's submit succeeds; the other 49 either lose the SELECT FOR UPDATE race and see the post-submit terminal state (409), or have the wrong actor identity and get 403. The sweep-vs-submit race is more subtle: a slow agent might be mid-`submit_job` when the auto-release sweep flips the Job back to `ready`. The locked behavior is "sweep wins, submit fails atomically" — no partial D/L state, no orphaned audit row, no zombie state.

**Scope (in):**
- **FastMCP version-pin smoke test (FIRST commit of Story 5.6)** per LD 22. Read `pyproject.toml` for the pinned FastMCP version. If it differs from cap-4's at-merge version (`fastmcp==2.14.7` per cap-04-plan), run the cap-4 multi-part spike pattern again (5-line test that registers a tool returning `list[Content]`, asserts the response shape arrives). Capture in `artifacts/cap-05/fastmcp-version-pin-check.txt`. If signature changes, amend cap-5 plan with one-line clarification BEFORE wiring `submit_job`'s multi-part response. (Same shape stayed → trivial pass-through.)
- `apps/api/src/aq_api/mcp.py` (modify) — update server-instructions block. Replace cap-4's `"submit_job (cap #5 — not yet shipped)"` text with:
  ```
  - Submit a finished Job via `submit_job(job_id, payload)` with one of four outcomes:
      done | pending_review | failed | blocked. The payload's shape per outcome
      is described in the tool description. AQ validates the payload against
      the Job's inline `contract` field; mismatches return error_code=`contract_violation`
      with a `details` object naming the offending field.
  - Resolve a `pending_review` Job via `review_complete(job_id, final_outcome)`.
      Any actor with a valid key can call this; the reviewing actor is recorded.
      final_outcome ∈ {done, failed} only.
  - `submit_job` accepts inline Decisions and Learnings via `decisions_made[]` and
      `learnings[]` arrays. Non-empty entries become rows attached to the submitting
      Job, returned as `created_decisions[]` and `created_learnings[]` in the response.
  ```
- `apps/api/src/aq_api/mcp.py` — refine `submit_job` tool description with per-outcome required-field summary + example for each.
- `tests/atomicity/test_submit_concurrent_race.py` (new) — 50 concurrent HTTP clients with mixed actor identities (1 legitimate claimant, 49 imposters across 5 fake actors), all calling `POST /jobs/{id}/submit` against ONE `in_progress` Job. Asserts:
  - Exactly 1 success (HTTP 200, state→`done`).
  - Exactly 1 success audit row (`op='submit_job', error_code=NULL`).
  - Remaining 49 are denials: 403 `submit_forbidden` (wrong actor) or 409 `job_not_in_progress` (lost the race after the winner committed). Each denial writes one audit row.
  - Total audit row count = 50 (1 success + 49 denials).
  - DB query: `SELECT count(*) FROM jobs WHERE id=:id AND state='done'` returns 1.
- `tests/atomicity/test_submit_sweep_race.py` (new) — sweep-vs-submit race. Setup: claim a Job with `AQ_CLAIM_LEASE_SECONDS=60` (test-only override); set `claim_heartbeat_at` to 70s ago via direct DB UPDATE (simulating a stale claim). Two concurrent operations: (a) `run_claim_auto_release_once(now=<future>)` (sweep fires); (b) `submit_job(...)` from the original claimant. Test the two interleavings:
  - **Sweep wins** (test forces this via a barrier in the sweep's UPDATE): submit's SELECT FOR UPDATE blocks; once sweep commits (state=`ready`, claim fields NULL, audit row written for `claim_auto_release`), submit's SELECT proceeds, sees state=`ready`, raises `BusinessRuleException(409, 'job_not_in_progress', ...)`. Submit's audit row is the denial. NO D/L rows, NO partial state. **Total post-state**: state=`ready`, 1 sweep audit + 1 submit-denial audit, 0 D/L rows.
  - **Submit wins**: submit's UPDATE commits first; sweep's later query finds state=`done` (no longer `in_progress`), no auto-release happens. **Total post-state**: state=`done`, 1 submit-success audit + (whatever sweep recorded if it ran on a different Job).
- `tests/atomicity/test_submit_blocked_edge_atomicity.py` (already in 5.3 scope; verify present in this story's regression run).
- `apps/api/tests/test_mcp_richness_cap05.py` (new or modify) — assert MCP `tools/list` returns `submit_job` and `review_complete` with `destructiveHint=true`, `readOnlyHint=false`, `idempotentHint=false`. Assert MCP `initialize` response contains the updated server `instructions` text (greps for "submit_job" + "review_complete" + "decisions_made" + "learnings"). Assert `submit_job` MCP call returns a **2-block content list (Job + text)** per LD 4 / gate-3 P1-2 — audit-row reference block was dropped because `audited_op` doesn't expose the inserted row ID back to the service.

**Scope (out):**
- No new ops.
- No `capabilities.md` fix-up (Story 5.7).
- No final evidence pack (Story 5.7).

**Verification:**
```
docker compose exec -T api uv run pytest -q tests/atomicity/test_submit_concurrent_race.py tests/atomicity/test_submit_sweep_race.py
docker compose exec -T api uv run pytest -q apps/api/tests/test_mcp_richness_cap05.py
docker compose exec -T api uv run pytest -q tests/parity/test_four_surface_parity.py    # full parity rerun
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/mcp.py
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.6-01 | MCP `initialize` response contains updated server `instructions` text referencing submit_job + review_complete + decisions_made + learnings | test | `artifacts/cap-05/mcp-instructions-cap05.xml` | greps pass |
| DOD-AQ2-S5.6-02 | `submit_job` and `review_complete` in MCP `tools/list` have `destructiveHint=true, readOnlyHint=false, idempotentHint=false` | test | `artifacts/cap-05/mcp-annotations-cap05.xml` | live MCP client asserts |
| DOD-AQ2-S5.6-03 | All cap-4 mutation tools STILL have the same annotations (regression) | test | same | live MCP client iterates |
| DOD-AQ2-S5.6-04 | `submit_job` MCP response is a **2-block content list (Job + text)** per LD 4 / gate-3 P1-2 (audit-row reference block dropped) | test | `artifacts/cap-05/mcp-submit-multipart.xml` | live MCP client asserts 2 blocks, NOT 3 |
| DOD-AQ2-S5.6-05 | Concurrent submit race (50 attempts, 1 legitimate claimant): exactly 1 success + 49 denials (403 submit_forbidden + 409 job_not_in_progress) | test | `artifacts/cap-05/race-50-concurrent-submit.xml` | pytest asserts counts |
| DOD-AQ2-S5.6-06 | Concurrent submit race: total audit rows = 50 (1 success + 49 denials); `SELECT count(*) FROM jobs WHERE id=:id AND state='done'` = 1 | test | same | pytest asserts |
| DOD-AQ2-S5.6-07 | Sweep-wins-race: submit returns 409 `job_not_in_progress` with zero partial state — no D/L rows, no state change attributable to submit, exactly 1 sweep audit + 1 submit-denial audit | test | `artifacts/cap-05/sweep-vs-submit-sweep-wins.xml` | pytest asserts |
| DOD-AQ2-S5.6-08 | Submit-wins-race: submit returns 200, state=`done`, sweep's later query no-ops on the now-terminal Job | test | `artifacts/cap-05/sweep-vs-submit-submit-wins.xml` | pytest asserts |
| DOD-AQ2-S5.6-09 | FastMCP version-pin smoke test passes against the cap-5-merge version of FastMCP | spike + artifact | `artifacts/cap-05/fastmcp-version-pin-check.txt` | spike output |

**Depends on:** Stories 5.2, 5.3, 5.4, 5.5.

**Submission shape (ADR-AQ-030):** Sixth commit. `dod_results[]` = 9 entries. `handoff = "AQ2-S5.7 (Story 5.7 — Evidence pack + capabilities.md fix-up + C2)"`.

---

### Story 5.7 — Evidence pack + `capabilities.md` fix-up + C2 checkpoint

**Objective:** Run the full Docker test matrix, mypy `--strict`, ruff. Verify post-migration DB state. Commit the EXPLAIN evidence for the two locked query shapes. Apply the surgical `capabilities.md` fix-up (one commit, one file). Push the branch tip. Post comprehensive evidence on the cap-5 epic. C2 checkpoint fires here. **No PR yet** — Codex opens the PR after Ghost approves the evidence.

**Why this matters (human outcome):** The capability is done. Every DoD across stories 5.1–5.6 has artifact evidence. The `capabilities.md` prose matches the shipped reality (no stale `register_contract_profile` references; the inline-D&L semantics from Decision 4 are documented). Mario reviews the evidence, approves, and Codex opens one PR for all of cap #5.

**Scope (in):**
- `plans/v2-rebuild/artifacts/cap-05/` — directory with all evidence artifacts referenced across stories 5.1–5.6 + this story's roll-up:
  - `evidence-summary.md` — narrative overview citing every DoD's artifact pointer.
  - `final-test-matrix.txt` — stdout of the full Docker pytest run.
  - `final-mypy-strict.txt` — stdout of `mypy --strict apps/api/src/aq_api/`.
  - `final-ruff.txt` — stdout of `ruff check apps/api apps/cli`.
  - `final-db-shape.txt` — `\d decisions`, `\d learnings`, `\d jobs` (state CHECK still 8 values), `\d job_edges` (CHECK still 3 values), `SELECT count(*)` from each new table on a fresh test fixture.
  - `cap05-locks-grep.txt` — grep evidence that all Locked Decisions 1–22 are reflected in code.
- `plans/v2-rebuild/capabilities.md` (modify) — surgical fix-up:
  - **Line 304:** Replace "AQ runs JSON Schema validation against the Job's Contract Profile (per ADR-AQ-030)" with "AQ shape-validates the payload against the Job's inline `contract` JSONB per ADR-AQ-030 (no profile registry — dropped in cap-3.5 per `plan-update-2026-04-28.md` Decision 3)".
  - **Line 304** (continued): Replace "emits a Run Ledger entry" with "writes an audit row queryable as a 'run' via cap #7's `list_runs`/`get_run` (no separate Run Ledger table per `plan-update-2026-04-28.md` Decision 1)".
  - **Line 304** (continued): Append: "Per `plan-update-2026-04-28.md` Decision 4, `submit_job` accepts inline `decisions_made[]` and `learnings[]` arrays; non-empty entries create rows in the `decisions` and `learnings` tables attached to the submitting Job, in the same transaction. Cap #9 ships the standalone D&L ops + the inheritance lookups in `get_*` responses."
  - **Lines 315 + 318-323:** Replace the "Implements ops" block with the final 2-op shape (`submit_job` + `review_complete`); remove all references to `register_contract_profile` and `version_contract_profile`.
  - **Line 325** (Note 2026-04-28 paragraph): replace with a tight sentence: "Per `plan-update-2026-04-28.md` Decisions 1, 3, and 4: no Run Ledger table; no Contract Profile registry; inline D&L creation at submit time. Cap-5's locked shape is canonical."
  - **Line 334** (Validation summary): replace `register_contract_profile`/`version_contract_profile` test references with `submit_job` shape-validation tests; mention inline D&L creation visible via `decisions`/`learnings` tables; mention cap-5's race tests + sweep-vs-submit atomicity.
- AQ2-73 Plane comment: "Cap-5 explicitly out of scope for Pipeline closure. Cap-5 ships and the gap is acknowledged. AQ2-73 stays backlog; resolved before cap #6 dogfood."

**Scope (out):**
- The PR itself (Codex opens after Ghost approval).
- Any new ops.

**Verification:**
```
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/
docker compose exec -T api uv run ruff check apps/api apps/cli
docker compose exec -T db psql -U aq -d aq2 -c "\d decisions"
docker compose exec -T db psql -U aq -d aq2 -c "\d learnings"
docker compose exec -T db psql -U aq -d aq2 -c "\d job_edges"   # CHECK still 3 values
grep -n "register_contract_profile\|version_contract_profile" plans/v2-rebuild/capabilities.md   # zero hits after fix-up
grep -n "decisions_made\[\]\|learnings\[\]" plans/v2-rebuild/capabilities.md   # at least one hit
```

**DoD items (ADR-AQ-030):**
| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5.7-01 | Full Docker test matrix passes (cap-1 + cap-2 + cap-3 + cap-3.5 + cap-4 + cap-5) | command | `artifacts/cap-05/final-test-matrix.txt` | green pytest output |
| DOD-AQ2-S5.7-02 | mypy `--strict` clean across `apps/api/src/aq_api/` | command | `artifacts/cap-05/final-mypy-strict.txt` | clean |
| DOD-AQ2-S5.7-03 | ruff clean across `apps/api apps/cli` | command | `artifacts/cap-05/final-ruff.txt` | clean |
| DOD-AQ2-S5.7-04 | `capabilities.md` cap-5 prose matches Locked Decisions 1–22 (no stale `register_contract_profile`/`version_contract_profile`; D&L inline-create documented) | grep | `artifacts/cap-05/capabilities-md-greps.txt` | all greps pass |
| DOD-AQ2-S5.7-05 | EXPLAIN evidence committed for both locked query shapes (decisions attached, learnings attached) | artifact | `artifacts/cap-05/explain-*.txt` (2 files) | each file shows the expected index access |
| DOD-AQ2-S5.7-06 | All artifacts under `artifacts/cap-05/` redacted via `scripts/redact-evidence.sh` before commit | command | `artifacts/cap-05/redaction-pass.txt` | gitleaks scan of artifacts dir clean |
| DOD-AQ2-S5.7-07 | AQ2-73 commented as out-of-scope-for-cap-5 (referenced from cap-5 evidence pack) | command | `artifacts/cap-05/aq2-73-status.txt` | comment text grep-verifiable |
| DOD-AQ2-S5.7-08 | CI test workflow green (NOT just local Docker) — module imports cleanly without DB envs | command | GitHub Actions run | green CI on the cap-5 PR branch tip |

**Depends on:** Stories 5.1, 5.2, 5.3, 5.4, 5.5, 5.6.

**Submission shape (ADR-AQ-030):** Seventh commit. `dod_results[]` = 8 entries. `handoff = "C2 checkpoint — evidence posted on cap-5 epic; awaiting Ghost approval. Codex opens PR after approval."`. **CHECKPOINT C2 fires here**.

---

## MCP richness for cap #5

Carries forward cap #4's locked pattern. Cap #5 ships the additions:

1. **Server-level instructions** updated to reference `submit_job` + `review_complete` as canonical exit paths (replacing cap-4's "submit_job ships in cap #5" placeholder). Includes the `decisions_made[]` / `learnings[]` inline-create semantics.
2. **Tool annotations** — both new mutations: `{"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False}`.
3. **Tool descriptions** — auto-derived from Pydantic field docstrings + per-op "why-to-use / when-to-use" line. `submit_job`'s description spells out the 4 outcomes + per-outcome required-field summary. `review_complete`'s description names the any-actor + pending_review-only constraints.
4. **Output content bundling** — `submit_job` returns a 3-block FastMCP content list: Job JSON + audit-row reference JSON + text block ("Job is now `<outcome>`. If `outcome=blocked`, the gated_on edge was inserted; cap #10 will auto-promote when the gating Job completes. If `decisions_made[]`/`learnings[]` were non-empty, see `created_decisions`/`created_learnings` in the response — cap #9 will surface them via `get_*` inheritance lookups."). `review_complete` returns single Pydantic dump (no navigation context).
5. **Tool input-schema field descriptions** — every Pydantic field on every cap-5 model carries a docstring; FastMCP auto-derives JSON Schema descriptions.

**Resources and Prompts** continue to be deferred:
- **Resources** land in cap #11 (Pipeline / ADR / Learning resources by URI).
- **Prompts** land in cap #6 dogfood (one prompt template `/aq-claim-and-work` extending to `/aq-claim-work-submit`).

---

## Hard checkpoints

- **C1 — after Story 5.2 lands.** `submit_job(outcome=done)` works end-to-end across REST + CLI + MCP. Parity test green for `-k submit`. Race + atomicity + sweep-race tests deferred to Story 5.6, but the single-actor happy path + every contract-violation rejection are covered. Codex stops, posts evidence on cap-5 epic, awaits Ghost approval before Story 5.3.
- **C2 — after Story 5.7 lands.** All 7 stories complete. Full Docker stack healthy. 2 cap-5 ops + inline D&L creation + `gated_on` edge insertion covered by parity + race + atomicity + sweep-race tests. `capabilities.md` fix-up applied. AQ2-73 commented. Codex stops, posts evidence on cap-5 epic, awaits Ghost approval before opening PR.
- **PR open + Ghost merge approval.** Codex opens ONE PR (squash-merges cap-5 onto `main`). Awaits Ghost merge approval. Does NOT self-merge.

---

## Capability-level DoD list

The 79 DoD items embedded in stories 5.1–5.7 above plus these capability-wide DoDs:

| ID | Statement | Verification | Evidence |
|---|---|---|---|
| DOD-AQ2-CAP5-01 | Both cap-5 ops surface on REST + CLI + MCP with byte-equal payloads | parity test | `artifacts/cap-05/four-surface-equivalence.txt` |
| DOD-AQ2-CAP5-02 | Cap #1 + Cap #2 + Cap #3 + Cap #3.5 + Cap #4 tests still pass unchanged | pytest | `artifacts/cap-05/regression-cap01-04.txt` |
| DOD-AQ2-CAP5-03 | `_assert_commit_matches_head()` invariant against authenticated `/version` on cap-5 branch tip | command | `artifacts/cap-05/commit-matches-head.txt` |
| DOD-AQ2-CAP5-04 | All cap-2 / cap-3 / cap-4 locks still present (sanity grep — `audited_op`, HMAC lookup_id, GIN index, partial-btree indexes, claim_heartbeat_at, system sweeper actor) | grep | `artifacts/cap-05/carry-forward-locks.txt` |
| DOD-AQ2-CAP5-05 | No `/audit` Web view introduced (Pact lock — UI is read-only) | grep | `artifacts/cap-05/web-routes.txt` |
| DOD-AQ2-CAP5-06 | Cap #5 introduces zero new env vars | command | `artifacts/cap-05/env-diff.txt` |
| DOD-AQ2-CAP5-07 | `error_code IS NULL` on every cap-5 success audit row (per LD 6 — outcome lives in response_payload, not error_code) | DB query | `artifacts/cap-05/success-error-code-null.txt` — `SELECT count(*) FROM audit_log WHERE op IN ('submit_job', 'review_complete') AND error_code IS NOT NULL AND response_payload->>'error' IS NULL` returns 0 |
| DOD-AQ2-CAP5-08 | `job_edges.edge_type` CHECK constraint still has only 3 values (no `job_references_*` added) | DB query | `artifacts/cap-05/edge-type-check-unchanged.txt` |
| DOD-AQ2-CAP5-09 | No standalone D&L ops shipped (cap #9 owns those) — grep `apps/api/src/aq_api/services/` for `create_decision`/`submit_learning`/`list_decisions`/`list_learnings`/`get_decision`/`get_learning`/`supersede_decision`/`edit_learning` returns zero hits | grep | `artifacts/cap-05/no-cap9-ops.txt` |
| DOD-AQ2-CAP5-10 | No `link_jobs`/`unlink_jobs`/`list_job_edges` ops shipped (cap #10) | grep | same | zero hits |
| DOD-AQ2-CAP5-11 | `get_job`/`get_pipeline`/`get_project` response inheritance arrays are still empty (cap #9 wires them) | test | `artifacts/cap-05/get-empty-inheritance.xml` | live test asserts arrays are `[]` post-cap-5 |
| DOD-AQ2-CAP5-12 | Pipeline closure semantics NOT introduced — AQ2-73 stays backlog (cap-5 explicitly out of scope) | command | AQ2-73 ticket state | "backlog" |
| DOD-AQ2-CAP5-13 | Every successful `submit_job` clears `claimed_by_actor_id`, `claimed_at`, `claim_heartbeat_at` per LD 7 — DB query asserts no `state IN ('done','failed','blocked','pending_review')` Job has any of the three claim fields populated | DB query | `artifacts/cap-05/no-stale-claims.txt` — `SELECT count(*) FROM jobs WHERE state IN ('done','failed','blocked','pending_review') AND (claimed_by_actor_id IS NOT NULL OR claimed_at IS NOT NULL OR claim_heartbeat_at IS NOT NULL)` returns 0 |
| DOD-AQ2-CAP5-14 | `BusinessRuleException(details=...)` is backward-compatible: cap-1/2/3/4 callers' identical JSON shape preserved when `details` not set | regression test | `artifacts/cap-05/exception-details-regression.xml` (from Story 5.2) |
| DOD-AQ2-CAP5-15 | `SubmitJobResponse` schema does NOT include `audit_row_id` field (per LD 4 / gate-3 P1-2) — grep response model returns no `audit_row_id` member | grep | `artifacts/cap-05/no-audit-row-id.txt` | grep returns 0 hits in `models/jobs.py` |

---

## Validation summary

Run `scripts/validate-cap05.sh` end-to-end. The script:
1. `docker compose down --remove-orphans && docker compose build && docker compose up -d --wait` (NEVER `down -v`; preserves named volume).
2. `alembic upgrade head` then verify the new tables + indexes.
3. Bootstrap a founder via `aq setup`. Create a Project, Pipeline, two Jobs (one with a populated `contract.dod_items[]`, one with empty).
4. Walk the submit graph:
   - Claim Job 1 → submit `outcome=done` with full payload + 2 decisions + 1 learning → state=`done`, response carries 2+1 IDs, audit row written, `decisions`/`learnings` tables have 2+1 new rows attached to Job 1.
   - Claim Job 2 (empty contract) → submit `outcome=failed` with `failure_reason='blocker discovered'` → state=`failed`, audit `error_code=NULL`.
   - Create + claim Job 3 → submit `outcome=pending_review` with non-empty `decisions_made[]` → state=`pending_review`, D&L rows present. From a different actor's key, `aq job review-complete <job-3-id> --final-outcome done` → state=`done`, audit row records the reviewing actor.
   - Create + claim Job 4 + Job 5 (Job 5 in same project) → submit Job 4 with `outcome=blocked, gated_on_job_id=<job-5-id>` → state=`blocked`, `job_edges` has the `gated_on` row. Try submit again with same gating Job → 409 `gated_on_already_exists`. Try submit with cross-project Job → 409 `gated_on_invalid:cross_project`. Try submit with self → 409 `gated_on_invalid:self`.
   - Try submit `outcome=done` on a `done` Job → 409 `job_not_in_progress`.
   - Try submit as wrong actor → 403 `submit_forbidden`.
   - Try submit with bad `dod_id` → 422 `contract_violation:dod_id_unknown`.
   - Try submit `outcome=done` with one DoD `failed` → 422 `contract_violation:incomplete_dod`.
   - Cancel a `pending_review` Job → state=`cancelled` (LD 7 carry-forward).
5. Run all Docker pytest suites (`apps/api/tests`, `apps/cli/tests`, `tests/parity`, `tests/atomicity`).
6. Run the concurrent-submit race test (50 attempts).
7. Run the sweep-vs-submit race test (both interleavings).
8. `EXPLAIN` the two locked query shapes; commit plans.
9. gitleaks v8.30.1 full-history scan.
10. `redact-evidence.sh` over every artifact before commit.

---

## Submission shape

- **Single branch** `aq2-cap-05` off `main` at `7f65654` (or whatever post-merge tip exists when claimed; verify with `git log origin/main --oneline -3` at branch creation).
- **Story-by-story commits** (7 commits, each story = one commit).
- **ONE PR at the end.** Codex stops at C2 for Ghost evidence review, then opens one PR.
- **Each story closes its child ticket** via `plane_update_status` with closeout comment.
- **Strict ADR-AQ-030 evidence per story** under `plans/v2-rebuild/artifacts/cap-05/`, redacted via `scripts/redact-evidence.sh` before commit.
- **`capabilities.md` fix-up** rolls into Story 5.7's commit (NOT a separate doc PR — fix-up is part of the cap-5 PR per the cap-4 precedent).

---

## Risks / deviations (declared in submission)

1. **`capabilities.md` cap-5 prose has known stale text.** Lines 304, 315, 318-323, 325, 334. Story 5.7 ships the surgical fix-up. The fix-up is part of the cap-5 PR, not a separate doc PR — agents reading the doc between merge of cap-4 and merge of cap-5 see the stale text; this is acknowledged. The `plan-update-2026-04-28*.md` files remain authoritative on conflict per the rev-4 banner.

2. **`error_code=NULL` on `outcome=failed` submits — deviation from cap-4's success-with-diagnostic-code precedent.** Cap-4 LD 21 used `error_code='lease_expired'` on a successful sweep audit row. Cap-5 LD 6 says: do NOT use `error_code` for outcome encoding. `outcome='failed'` is a successful submit (the agent reported failure correctly); the outcome lives in `response_payload`. `error_code` is reserved for denial paths. This is a deliberate choice — keeps `error_code` semantically meaning "the operation was rejected" rather than "the work is bad." Future audit-redactor design must read `op + outcome + error_code` together to distinguish "submit succeeded reporting failure" from "submit was denied."

3. **Inline D&L creation is the single largest cap-5 service complexity.** A submit with N decisions + M learnings runs N+M+1 INSERT statements + 1 UPDATE + 1 audit-row INSERT in a single transaction. Atomicity test (Story 5.5) covers three failure-injection points. If cap-5 ships and a high-N submission proves slow at cap-6 dogfood time, Story 5.5's helper `_insert_inline_dl` is structured to allow batched INSERT (`INSERT INTO ... VALUES (...), (...), ...` form) without API-shape change — file as a follow-up gap-ticket if dogfood reveals a real perf concern.

4. **No FK from `decisions.attached_to_id` / `learnings.attached_to_id` to `jobs(id)` / `pipelines(id)` / `projects(id)`.** The column is polymorphic across three target tables, so a single FK doesn't fit. Application-level enforcement at insert time (cap-5 only writes `'job'` so the only enforcement is "Job must exist," which the `submit_job` SELECT FOR UPDATE already proves). Cap #9's standalone ops will need their own validation. Documented as a known soft-link.

5. **`gated_on` edge insertion at submit time NOT mediated by a public `link_jobs` op.** Cap-5 inserts directly into `job_edges`. Cap #10 will ship `link_jobs` over the same table. The two must agree on column shape — locked at the migration level (cap-3.5's `0005` migration already shipped the table; cap-5 doesn't touch its shape). Future `link_jobs` design must accept that cap-5 may have inserted rows before cap-10's validation rules existed.

6. **Cycle detection on `gated_on` edges deferred to cap #10.** Per Codex Q3: cap-5's `submit_job(outcome=blocked)` does NOT check whether the new edge creates a cycle (e.g., A blocked-on B, B blocked-on A). If it does, cap-5 just persists the edge; cap-10's `list_descendants` / `list_ancestors` ops surface `cycle_detected: true` on traversal. Cycle prevention at link time is a v1.2+ backlog item per `plan-update-2026-04-28-graph.md` Section 6.

7. **Pipeline closure NOT introduced.** Filed as AQ2-73. Cap-5 closes only the Job loop; the parent Pipeline (e.g., the cap-4 Pipeline housing 7 child Job-equivalents) has no machine-checkable terminal state after cap-5. Resolved before cap #6 dogfood per AQ2-73.

8. **`submit_job` request payload may be large** (full ADR-AQ-030 submission can exceed 10KB with many DoD results + decisions + learnings). The same-tx redactor (cap-2 lock) operates recursively across the payload — perf is acceptable at v1 scale per cap-2 evidence. If cap-6 dogfood reveals a real perf concern, request-size limits become a v1.1 plan-update item.

9. **No client-side cadence enforcement on `review_complete`.** A `pending_review` Job can sit indefinitely. The auto-release sweep does NOT touch `pending_review` (only `in_progress` per cap-4 LD 11). If a workflow needs an SLA on review, that's caller-side scheduling — NOT cap-5's responsibility.

10. **`SubmitJobRequest` discriminated union — Pydantic 2 specific.** Pydantic 1 doesn't support discriminator the same way; cap-5 commits to Pydantic 2 (already in `pyproject.toml` per cap-1 lock). Migration to a future Pydantic version will need a sweep of the discriminator declarations.

11. **Web tier has zero changes in cap #5.** The two cap-5 ops are API-only. Cap #11 owns UI. Parity tests skip Web assertions for the two cap-5 ops (existing pattern from cap-3 + cap-4).

12. **`BusinessRuleException` extension is foundational** (per gate-3 P1-3 / LD 23). Cap-5 modifies `apps/api/src/aq_api/_audit.py` — a file every cap-2/3/4 mutation imports. Default `details=None` preserves existing caller behavior; backward-compat is asserted by Story 5.2's regression test (DOD-AQ2-S5.2-16). If the regression test discovers any cap-1/2/3/4 caller has been depending on the EXACT `{"error": exc.error_code}` shape with no extra keys (e.g., `assert response.json() == {"error": "..."}` strict-equality), update the test instead of the production code — the contract is "JSON with at least these keys," not "JSON with exactly these keys."

13. **Validation order swap (auth-after-state) is a deliberate deviation from the cap-2/cap-4 default** (per gate-3 P1-5 / LD 5). Cap-2 / cap-3 / cap-4 ops generally check authorization before state because their target rows always have a stable claimant (or have no claimant concept at all). Cap-5 inverts this because: with claim fields cleared on every successful submit (LD 7), a terminal Job has `claimed_by_actor_id=NULL`. An auth-first check would return `403 submit_forbidden` (since `NULL != actor_id`) but the locked error code is `409 job_not_in_progress`. State-first matches the locked error table (LD 11). Documented as code comment in `services/submit.py`'s validation block.

14. **Story 5.2 commit discipline locked: one squashed final commit, internal local-checkpoint sub-flow allowed.** Story 5.2 is intentionally heavier than other cap-5 stories (`BusinessRuleException(details=)` extension + route serialization + `_audit.py` denial-path merge + `submit_job(outcome=done)` service + inline D&L creation + REST + CLI + MCP + parity regen + 18 DoDs). Per Codex's gate-3 follow-up, Story 5.2 is NOT split into 5.2a/5.2b at the Plane-ticket level — splitting them would ship infra (`details=`) with no consumer, and the regression coverage would degrade to "no break in cap-1/2/3/4" without proving the new field works end-to-end. **Instead: the Story 5.2 ticket body explicitly authorizes a local-checkpoint sub-flow** — Codex sequences implementation in 6 internal steps (BRE extension → contract validator → submit service → CLI/MCP wiring → D&L helper → parity regen) but **the final cap-5-branch commit is ONE squashed commit at the C1 boundary**. WIP commits stay on a private working branch; the cap-5-branch history records exactly one Story 5.2 commit. Preserves cap-4's "one commit per story" discipline at the cap-5-branch level. If any local checkpoint reveals a foundational decision needing revision, Codex pauses and pings Mario before continuing.

---

## Ready for execution

Rev 2 incorporates the gate-1 brief approval (15 Codex locks), gate-2 codebase survey (2 handoff corrections), AND gate-3 Codex audit (5 P1 + 2 P2 implementer traps fixed):

- **Gate 1 (brief, 2026-04-29):** approved by Mario + Codex with 15 locked decisions (Q1–Q15).
- **Gate 2 (codebase survey, 2026-04-29):** corrected handoff §8.1's claim that `job_references_*` enum values were already added in cap-3.5 (verified at `db.py:307` — they are NOT) and confirmed there's no `gated_on_job_id` column on `jobs`.
- **Gate 3 (Codex audit, 2026-04-29):** caught 5 P1 issues (claim-field clearing on submit, `audit_row_id` not implementable, `details` field not yet supported, validation rules contradiction across outcomes, validation-order error-code mismatch) and 2 P2 issues (impossible duplicate-edge test flow, Story 5.2 silently dropping D&L). All folded into rev 2.
- **Gemini audit:** independent verification of schema baseline + state CHECK + edge enum + ADR-AQ-030 strictness — passed at 95–98% confidence (Gemini, 2026-04-29).
- **Discovery filed: AQ2-73** (Pipeline closure semantics) explicitly out of cap-5 scope; resolved before cap #6.

Story-by-story execution discipline: Codex executes one story at a time, runs that story's verification commands, commits, then moves to the next. C1 (after Story 5.2) and C2 (after Story 5.7) are hard stops for `claude` audit before continuing. PR-open + Ghost merge approval is the final gate (no self-merge). This matches the cap-2 / cap-3 / cap-3.5 / cap-4 cadence.

**Plan-doc owner:** claude (Opus 4.7), 2026-04-29.
**Reviewers:** Mario (final approval), Gemini (audit passed), Codex (audit caught + fixed 7 issues; ready to implement at ~95% confidence).

---

## Critical files to modify (cap-5 file paths)

Reference list — paths checked against live codebase via the gate-2 survey. Each story's "Scope (in)" lists its specific files; this is the master roll-up.

| Path | Story | Action |
|---|---|---|
| `apps/api/alembic/versions/0007_cap05_decisions_and_learnings.py` | 5.1 | new |
| `apps/api/src/aq_api/models/db.py` | 5.1 | extend (Decision, Learning ORM) |
| `apps/api/src/aq_api/models/decisions.py` | 5.1 | new |
| `apps/api/src/aq_api/models/learnings.py` | 5.1 | new |
| `apps/api/src/aq_api/models/jobs.py` | 5.1 | extend (SubmitJob* + ReviewComplete*) |
| `apps/api/src/aq_api/models/__init__.py` | 5.1 | extend (re-exports) |
| `apps/api/src/aq_api/_audit.py` | 5.2 | extend (`BusinessRuleException(details=)` per LD 23; default None preserves cap-1/2/3/4 behavior) |
| `apps/api/src/aq_api/routes/_errors.py` (or wherever the BRE handler lives — locate at Story 5.2 first commit) | 5.2 | extend (route response body includes `details` only when set) |
| `apps/api/src/aq_api/services/submit.py` | 5.2, 5.3 | new + extend (D&L creation in 5.2; other 3 outcomes in 5.3) |
| `apps/api/src/aq_api/services/_contract_validator.py` | 5.2, 5.3 | new + extend |
| `apps/api/src/aq_api/services/review.py` | 5.4 | new |
| `apps/api/src/aq_api/routes/jobs.py` | 5.2, 5.3, 5.4 | extend (add submit + review_complete routes) |
| `apps/cli/src/aq_cli/main.py` | 5.2, 5.4 | extend (add submit + review-complete commands) |
| `apps/api/src/aq_api/mcp.py` | 5.2, 5.4, 5.6 | extend (register tools + update instructions) |
| `apps/api/tests/test_models_cap05.py` | 5.1 | new |
| `apps/api/tests/test_submit_job_done.py` | 5.2 | new |
| `apps/api/tests/test_submit_job_pending_review.py` | 5.3 | new |
| `apps/api/tests/test_submit_job_failed.py` | 5.3 | new |
| `apps/api/tests/test_submit_job_blocked.py` | 5.3 | new |
| `apps/api/tests/test_submit_job_blocked_edge_atomicity.py` | 5.3 | new (pre-seeded edge fixture per gate-3 P2-1) |
| `apps/api/tests/test_submit_job_blocked_pydantic_excludes_dod_results.py` | 5.3 | new (per gate-3 P1-4) |
| `apps/api/tests/test_business_rule_exception_details.py` | 5.2 | new (per LD 23 regression coverage) |
| `apps/api/tests/test_review_complete.py` | 5.4 | new |
| `apps/api/tests/test_state_machine_completeness_cap05.py` | 5.4 | new |
| `apps/api/tests/test_submit_inline_dl_happy.py` | 5.5 | new |
| `apps/api/tests/test_submit_inline_dl_atomicity.py` | 5.5 | new (5 failure-injection scenarios) |
| `apps/api/tests/test_submit_inline_dl_review_complete_interaction.py` | 5.5 | new |
| `apps/api/tests/test_submit_dl_per_attached_kind.py` | 5.5 | new (cap-9 forward-compat sanity) |
| `apps/api/tests/test_mcp_richness_cap05.py` | 5.6 | new |
| `tests/atomicity/test_submit_concurrent_race.py` | 5.6 | new |
| `tests/atomicity/test_submit_sweep_race.py` | 5.6 | new |
| `tests/parity/openapi.snapshot.json` | 5.2, 5.3, 5.4 | regenerate |
| `tests/parity/mcp_schema.snapshot.json` | 5.2, 5.3, 5.4 | regenerate |
| `tests/parity/test_four_surface_parity.py` | 5.2, 5.3, 5.4 | extend |
| `plans/v2-rebuild/capabilities.md` | 5.7 | surgical fix-up (5 line ranges) |
| `plans/v2-rebuild/artifacts/cap-05/` | 5.1–5.7 | new directory + ~30 evidence files |

## Critical files to reuse (cap-5 references existing utilities)

| Existing file | Used for | Reference line(s) |
|---|---|---|
| `apps/api/src/aq_api/_audit.py` | `audited_op` four-path context manager — every cap-5 mutation enters it | `_audit.py:26-75` (no changes) |
| `apps/api/src/aq_api/_datetime.py` | `parse_utc` for any datetime field | cap-1 carry-forward |
| `apps/api/src/aq_api/_settings.py` | settings loader (no new env vars; just reads existing) | cap-2 + cap-4 carry-forward |
| `apps/api/src/aq_api/_auth.py` | short-lived auth session pattern | cap-4 LD 17 — preserve |
| `apps/api/src/aq_api/services/jobs.py` | `job_from_db` helper for response Pydantic conversion | `services/jobs.py:53-68` |
| `apps/api/src/aq_api/services/job_lifecycle.py` | `cancel_job` + `TERMINAL_STATES` set | `services/job_lifecycle.py:14` (LD 7 verifies allows pending_review) |
| `apps/api/src/aq_api/services/audit.py` | `record(...)` audit-writer | invoked by `audited_op` automatically |
| `apps/api/src/aq_api/models/db.py` (existing) | `DbJob`, `DbJobEdge` ORM imports | `db.py:231-329` |
| `apps/api/src/aq_api/models/inheritance.py` | `InheritanceReferenceLists` (already imported by `GetJobResponse`; cap-5 doesn't touch) | `models/inheritance.py:6-8` |
| `apps/api/tests/_isolated_schema.py` | test fixture isolation for D&L + audit_log mutations | `_isolated_schema.py:26-72` (cap-4 lock — required usage) |
| `scripts/redact-evidence.sh` | redact artifacts before commit | cap-2 carry-forward |

---

## Verification (end-to-end, after Story 5.7 lands)

```
# Real-stack verification (Codex runs before posting C2 evidence)
docker compose down --remove-orphans
docker compose build
docker compose up -d --wait

# Schema delta
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
docker compose exec -T db psql -U aq -d aq2 -c "\d decisions"
docker compose exec -T db psql -U aq -d aq2 -c "\d learnings"
docker compose exec -T db psql -U aq -d aq2 -c "\d job_edges"     # CHECK still 3 values

# Alembic round-trip
docker compose exec -T api uv run alembic -c apps/api/alembic.ini downgrade -1
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head

# Full pytest matrix
docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity

# Type + lint
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/
docker compose exec -T api uv run ruff check apps/api apps/cli

# End-to-end MCP loop (claim → submit done with D&L)
mcp__agenticqueue__claim_next_job project=<id> agent_identity=<key>
mcp__agenticqueue__submit_job job_id=<id> payload=@closeout.json agent_identity=<key>
mcp__agenticqueue__get_job job_id=<id> agent_identity=<key>     # state=done; check decisions/learnings still empty in get_job (cap #9 wires inheritance)

# DB sanity (after E2E)
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM decisions WHERE attached_to_kind='job'"
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM learnings WHERE attached_to_kind='job'"
docker compose exec -T db psql -U aq -d aq2 -c "SELECT op, count(*) FROM audit_log WHERE op IN ('submit_job', 'review_complete') GROUP BY op"
docker compose exec -T db psql -U aq -d aq2 -c "SELECT count(*) FROM audit_log WHERE op='submit_job' AND error_code IS NOT NULL AND response_payload->>'error' IS NULL"   # =0 per LD 6

# CI verification (NOT just local Docker — cap-4 caught a regression here)
git push origin aq2-cap-05    # triggers GitHub Actions test workflow
gh run watch                   # Codex watches; if test workflow red, Story 5.7 is blocked
```

---

## Out-of-band notes

- The handoff doc estimated 7 stories with C1 after `submit_job(done)` and C2 after evidence pack. Confirmed; this plan matches.
- The handoff doc estimated 20–25 new tests. Concrete count: ~14 new test files containing approximately 60–80 individual test cases (covering happy paths, validation rejections, atomicity, race, parity, state matrix). Final count emerges in evidence.
- AQ2-72 (`claim_job(job_id)` direct-claim) backlog ticket: when cap #5 ships, AQ2-72's plan can be amended to use the natural `submit_job(outcome=blocked)`/`submit_job(outcome=pending_review)` transitions instead of the SQL state-flip its DoD-CLAIMID-03c/03d originally specified. This is a one-line note added to AQ2-72 by Story 5.7's evidence pack ("AQ2-72 plan can use cap-5 transitions; the SQL-flip workaround is no longer necessary post-cap-5").
- AQ2-60 (cross-agent process-memory delivery design) becomes more relevant post-cap-5 because cap-5 ships Project-attached Learning capacity (the schema admits `attached_to_kind='project'`, even though cap-5's submit only writes `'job'`). Project-level Learnings become the natural home for "rules every agent following work in this Project should know" once cap #9 ships standalone D&L ops. Worth a Mario ping during cap-5 planning. **Not blocking.**
