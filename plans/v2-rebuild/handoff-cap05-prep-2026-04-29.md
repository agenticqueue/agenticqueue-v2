# Handoff to next opus — Cap #5 planning prep

Filed 2026-04-29 by claude (Opus 4.7) after cap #4 merged to `main` at squash-merge commit `de1febd91074c6b8ed0ae72bcee3bac895c58cfb` plus the AQ2-70 test-hygiene follow-up at `7f6565415a3c55564b7c4e332283588973d45d92`. This is your first read. Treat it as a session-start orientation. Read it once end-to-end, then keep it open as a reference while you plan cap #5.

You have **zero context**. The doc below is structured to give you exactly what you need to plan cap #5 without re-deriving everything from scratch.

---

## 1. What AQ2 is, in one paragraph

AQ2 (AgenticQueue 2.0) is a single-instance work coordination tool for AI agents. A human (Mario, "Ghost") creates a Project, defines work as Pipelines containing Jobs, and agents (Claude / Codex / Gemini) claim Jobs, do the work, and submit results. AQ2 enforces a contract on every Job — an inline JSONB `contract` document on the Job row that says "what does done look like?" — and keeps an immutable audit log of every mutation. The whole system is exposed on **four surfaces** that must serve byte-equal payloads: REST, CLI, MCP, and a read-only Web UI. This four-surface byte-equality is the foundational invariant; CI mechanically enforces it with parity tests. Cap-4 just shipped the atomic claim primitive ("pull, do not push" + heartbeat lease + auto-release sweep). Cap #5 closes the loop: a claimant submits the Job's outcome, AQ validates the submission's shape against the inline Contract, and the state machine + audit log advance correctly.

---

## 2. Status as of 2026-04-29

**Done + on `main`:**
- Cap #1 (four-surface ping) — merged 2026-04-26 at `96e158d`
- Cap #2 (Authenticated Actors + Bearer auth + same-transaction audit log) — merged 2026-04-27 at `dc4ad37`
- Cap #3 + Cap #3.5 (Project / Pipeline / Job entities with full CRUD; Workflow → Pipeline collapse; Contract Profile drop; inline `contract` JSONB; seeded `ship-a-thing` template) — merged 2026-04-28 at `c956f1d`
- **Cap #4 (atomic Job claim with heartbeat lease + auto-release sweep) — merged 2026-04-29 at `de1febd`**
- AQ2-70 test-hygiene cleanup (cap-4 fixture leakage fixed; isolated-schema pattern in `_isolated_schema.py`) — merged 2026-04-29 at `7f65654`

**Today's `main` HEAD:** `7f65654 [AQ2-70] Test hygiene: isolate cap-4 sweep + audited_op fixtures from dev DB`. Confirm with `git -C D:/mmmmm/mmmmm-aq2.0 fetch origin main && git log origin/main --oneline -3`.

**Next epic:** Cap #5 — A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly. **You plan it.**

---

## 3. Where things live

| Thing | Path |
|---|---|
| Code repo | `D:\mmmmm\mmmmm-aq2.0\` |
| Capability map (canonical spec) | `plans/v2-rebuild/capabilities.md` (rev 4 + cap-04 fix-up by Story 4.7) |
| Cap-4 plan (model for what your cap-5 plan should look like) | `plans/v2-rebuild/capability-04-plan.md` (820 lines, 22 Locked Decisions) |
| Cap-3 plan (the rev-3 historical-with-rev-4-banner shape; gold reference for plan-doc structure) | `plans/v2-rebuild/capability-03-plan.md` |
| Cap-2 + Cap-1 plans | `plans/v2-rebuild/capability-02-plan.md`, `plans/v2-rebuild/capability-01-plan.md` |
| Plan-update authority docs (override `capabilities.md` on conflict) | `plans/v2-rebuild/plan-update-2026-04-28.md` and `plans/v2-rebuild/plan-update-2026-04-28-graph.md` |
| Cap-4 prep handoff (the model for THIS doc) | `plans/v2-rebuild/handoff-cap04-prep-2026-04-28.md` |
| Cap-4 evidence pack (the model for what your C2 evidence should look like) | `plans/v2-rebuild/artifacts/cap-04/` |
| Test-hygiene cap-04 cleanup (the model for fixture isolation) | `plans/v2-rebuild/artifacts/test-hygiene/` + `apps/api/tests/_isolated_schema.py` |
| ADR (Architectural Decision Records) | `D:\mmmmm\mmmmm-agenticqueue\adrs\` |
| ADR-AQ-030 (the contract-checklist ADR — your validation contract) | `D:\mmmmm\mmmmm-agenticqueue\adrs\ADR-AQ-030-agent-ready-contract-checklist.md` |
| ADR-AQ-019 (the lexicon — defines every term) | `D:\mmmmm\mmmmm-agenticqueue\adrs\ADR-AQ-019-lexicon.md` |
| Per-story evidence | `plans/v2-rebuild/artifacts/cap-NN/<story-evidence>.{txt,xml,md}` |
| AGENTS.md (process rules) | `D:\mmmmm\AGENTS.md` |
| CLAUDE.md (claude-specific rules) | `D:\mmmmm\CLAUDE.md` |

**Plane** is the work-tracking system: `http://localhost:8502/mmmmm/`. Use `/browse/<TICKET-KEY>/` for direct ticket links. AQ2 is project `AQ2`. Tickets are filed there with the standardized ADR-AQ-030 shape (see Section 6).

---

## 4. The agents in the loop

Three agents do work, plus Mario as gate:

| Agent | Role |
|---|---|
| **Mario / Ghost** | Sole human operator. Approves at gates. Source of truth on scope decisions. Has been delegating audit-trail + merge work to claude routinely throughout cap-4 ("act on my behalf", "you choose", "B"). |
| **claude (you)** | Plans, audits, files tickets. Does NOT touch git on `aq2-cap-NN` branches during stories. Read-only on repo + Plane state transitions during audits. Has been doing the squash-merge for cap-4 + AQ2-70 on Mario's explicit delegation. |
| **Codex** | Implementer. Does ALL git operations on cap branches (commits, pushes). Moves Plane tickets to `done` after claude APPROVED. |
| **Gemini** | Independent auditor for plan rev cycles. Used for fresh-eye reviews on capability plans before they ship. |
| **Sonnet** | QA / mechanical sweeps. Used for tickets that are "execute this list and capture evidence" type work. |

This separation of concerns is real and standing. Don't change it without Mario's say-so. The merge-via-claude pattern is established for cap-4 + AQ2-70; you can expect the same for cap-5 unless Mario revokes.

---

## 5. The standing process — read this carefully

**Per-story flow (still applies):**

1. claude writes the ticket (ADR-AQ-030 shape — see Section 6).
2. Codex claims via Plane (`plane_update_status` to `in progress` + `agent:codex` label — note: `plane_claim_next` doesn't work; the Plane project uses `todo` not `queued`, so manual transition is the standing workaround).
3. Codex implements + pushes + posts a closeout JSON comment with `dod_results[]`.
4. claude audits live (re-runs verification commands from MY session, doesn't just trust evidence files; uses REST + MCP tool calls + DB queries).
5. claude posts an APPROVED or REJECTED comment.
6. Codex moves the ticket to `done` after seeing APPROVED. (claude does NOT move tickets — Codex owns story-level state transitions.)
7. Codex claims the next ticket.

**Capability gates:**
- C1 (mid-capability checkpoint): Codex stops, claude audits, Mario approves before resuming.
- C2 (end-of-capability checkpoint): same. Plus this is where the capability PR opens.
- C3 (PR merge): Mario merge approval. With cap-4 precedent, Mario has been delegating C2 sign-off + merge to claude. Don't pre-empt without explicit delegation.

**Hard rules:**
- mypy `--strict` + ruff + pytest must all be GREEN on EVERY push. Lint regression caught at audit blocks the commit until fixed.
- **CI test workflow must also be green**, not just local Docker. Cap-4 caught a regression where `app.py:16`'s `from aq_api._db import SessionLocal` broke CI test collection (no DB envs in CI). Fix path: lazy-import inside the lifespan / inside test files. Don't repeat that mistake.
- Validate live, don't trust evidence files alone. Mario flagged this multiple times: "verify, never trust."
- If you write a Plane ticket that references a file path or constraint name, validate against the live repo BEFORE publishing.
- Never run `docker compose down -v`. Never. Use isolated test DBs for fresh-install scenarios.
- Never commit secrets. Founder Bearer keys go in Vault at `secret/aq2/founder-key`. Configs read from env vars or Vault, never inline.
- **Use `apps/api/tests/_isolated_schema.py` for any test fixture that mutates `actors` / `audit_log` / shared cap-4 tables.** The cap-4 fixture-leakage pattern (44 deactivated `aq-system-sweeper` rows on dev DB) was painful to clean up — don't repeat it. Cap-5 D&L tests will heavily exercise actors + audit_log; they MUST use isolated schemas.

---

## 6. The ADR-AQ-030 ticket format

Every plan-story ticket in Plane uses this shape. Mario rejects non-conforming bodies on sight. The exact sections, in order:

```markdown
Parent: AQ2-NN (Capability #X epic)

## Why this matters (human outcome)

Two-three sentences explaining the user-visible change. Mario reads this first; if it's missing or vague he rejects.

## Objective

What this ticket implements. Be specific about ops, surfaces, and shape.

## Scope (in)

- Bullet list of files, ops, tests, evidence files.
- Be explicit about file paths.
- Validate paths against the live repo before publishing — this rule has a memory file.

## Scope (out)

- What this ticket explicitly does NOT do, with pointers to the story that DOES do it.

## Security guardrails

- Bullet list of cap-2 / cap-3 / cap-4 / cap-N locks that this ticket honors or extends.

## KISS/DRY

- Bullet list of reuse opportunities.

## Verification

```
docker compose exec -T api uv run pytest -q apps/api/tests/test_<thing>.py
docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/
docker compose exec -T api uv run ruff check apps/api apps/cli
```

## DoD items (ADR-AQ-030)

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S<n>-01 | <statement> | <command|test|review|grep|artifact> | `artifacts/cap-NN/<file>.xml` | <pass condition> |
| ... | | | | |

## Depends on

What other story/cap must be in `done` first.

## Submission shape (ADR-AQ-030)

`outcome ∈ {done, failed, blocked}`. `dod_results[]` = N entries. `files_changed[]` = `[<list>]`. `risks_or_deviations` = `[]` unless something hit. `handoff = "<the next ticket key + title>"`.
```

DoD IDs follow `DOD-AQ2-S<story-number>-<seq>` for stories, `DOD-AQ2-CAP<n>-<seq>` for capability-wide DoDs. Keep them grep-stable.

The 5 DoD columns are LOCKED. Don't add or remove columns. Mario checks this with grep.

---

## 7. The plan-update-2026-04-28 docs (still authoritative)

These two files override `capabilities.md` on conflict:

- **`plan-update-2026-04-28.md`** (Mario, 2026-04-28) — six structural decisions:
  1. Run Ledger collapses into audit_log (no separate table)
  2. Workflows collapse into Pipelines (`is_template` flag + `clone_pipeline` op) — shipped in cap-3.5
  3. Contract Profiles dropped entirely (inline `contract JSONB` on each Job) — shipped in cap-3.5
  4. **`submit_job` Contract requires `decisions_made[]` + `learnings[]` arrays** — **CAP-5 OWNS THIS.** Non-empty arrays cause `submit_job` to create Decision and Learning nodes inline in its transaction, attached to the Job. Cap #9 owns the standalone D&L ops; cap-5 owns the at-submit-time inline creation.
  5. Webhooks deferred to v1.1 (pull-only via MCP polling in v1)
  6. Read-only UI forever (single exception: `create_api_key` UI mint flow in cap #11)
- **`plan-update-2026-04-28-graph.md`** (Mario, 2026-04-28) — Decision 7:
  7. The graph becomes queryable. Cap #10 ships three traversal ops. `get_job` / `get_pipeline` / `get_project` ship with `decisions: {direct, inherited}` + `learnings: {direct, inherited}` arrays for cap #9 forward-compat (cap #3 already shipped these as empty arrays).

**For cap-5 specifically, Decision 4 is load-bearing.** It changes the submission shape AND requires you to create the `decisions` and `learnings` tables (cap-5 territory) so the inline-create-on-submit can persist. Cap #9 will then add the standalone D&L ops + the inheritance lookups in get_*.

These docs are your AUTHORITY when `capabilities.md` text is stale.

---

## 8. Cap #5 — what's locked and what you plan

**Verbatim from `capabilities.md` (rev 4 + cap-04 fix-up):**

> ### Capability #5: A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly
>
> **Statement:** A claimant calls `submit_job` with `outcome ∈ {done, pending_review, failed, blocked}` and a structured payload; AQ validates the payload against the Job's inline `contract` JSONB per ADR-AQ-030, checks state transition validity, commits the new state and audit row in one transaction, and (per `plan-update-2026-04-28.md` Decision 4) creates Decision and Learning nodes inline if `decisions_made[]` or `learnings[]` are non-empty. Pending-review Jobs can be transitioned to `done` or `failed` by any key via `review_complete`.
>
> **Why this is here:** The submission boundary is moat #5 — Contracts are schemas, not CI. Codex correction #4 (`update_job` is metadata only — transitions are explicit ops) is validated by this capability. Without this, a Job can be created and claimed but never finished — the loop doesn't close.
>
> **Depends on:** #4 (claim must exist before submit) — ✓ cap-4 is on main.
>
> **Implements ops:**
> - `submit_job(outcome, payload)` — claimant only. State transition: `in_progress → done | failed | blocked | pending_review`.
> - `review_complete(final_outcome ∈ {done, failed})` — any key. State transition: `pending_review → done | failed`.

**Locked decisions inherited from earlier caps that constrain cap #5:**

- **Inline `contract` JSONB** (cap-3.5): every Job carries `jobs.contract NOT NULL`. Validation at submit time uses this column, NOT a profile registry (which doesn't exist).
- **ADR-AQ-030 contract structure**: the JSON schema for valid contracts. Read it in full at `D:\mmmmm\mmmmm-agenticqueue\adrs\ADR-AQ-030-agent-ready-contract-checklist.md`.
- **`audited_op` four-path semantics** (cap-4 LD 4): success+normal-audit, success+skip, denial, unexpected. `submit_job` uses normal audit (every submission writes a row). `review_complete` same.
- **`AuditOperation.error_code` field** (cap-4 LD 21): supports success-with-diagnostic-code semantics. Used by cap-4's `claim_auto_release`. Cap-5 may use it for `submit_job(outcome=failed)` — open design call.
- **No `set_instructions` method on FastMCP** (cap-4 LD 22 + Story 4.6 spike): use `FastMCP(MCP_NAME, tasks=False, instructions=MCP_INSTRUCTIONS)` constructor pattern. Don't waste time discovering this.
- **MCP richness pattern** (cap-4 LD 18): every cap-5 mutation tool MUST set `{"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False}`. Same shape as cap-4 ops. `submit_job` returns multi-part content (Job + audit-row reference + next-step text) per the cap-4 precedent.
- **State machine** (cap-3 + cap-4 carry-forward):
  - `submit_job` valid only when state = `in_progress` AND caller = current claimant
  - `review_complete` valid only when state = `pending_review`
  - Cap-5 NEVER transitions to `ready` (that's release/reset/sweep — cap-4)
  - Cap-5 NEVER transitions out of `cancelled` / `done` / `failed` (terminal)
- **Heartbeat lease + sweep** (cap-4): if a claimant doesn't submit before `AQ_CLAIM_LEASE_SECONDS` (900s default) and stops heartbeating, the auto-release sweep flips them back to `ready` with `op='claim_auto_release'`. Cap-5's submit happens BEFORE the sweep can fire (claimant is actively heartbeating); but if there's a race where the sweep fires DURING submit, that's a real edge case worth covering in cap-5's atomicity tests.
- **`agent_identity` is decorative-only** (cap-2 lock): authentication continues to use Bearer; `agent_identity` is just an audit-log annotation.
- **Cap-3.5 + cap-4 schema is finalized**. Cap-5 adds tables (`decisions` + `learnings`); doesn't modify existing.

**Concrete things to design:**

1. **Schema delta — `decisions` and `learnings` tables.** Per Decision 4: each is a first-class graph node, attached to ONE entity (Job/Pipeline/Project). The attachment IS the scope. Tables need: `id` PK, `attached_to_kind` enum (`job` / `pipeline` / `project`), `attached_to_id` UUID, content fields (TBD per ADR-AQ-019 lexicon + Decision 4), `created_at`, `created_by_actor_id`. Plus the two new edge types `job_references_decision` and `job_references_learning` (already in the cap-3.5 enum from migration `0005_cap0305_schema_consolidation` — verify before re-adding).

2. **`submit_job` contract validation per ADR-AQ-030.** Read ADR-AQ-030 in full. The submission has required + conditional fields per outcome:
   - `done`: `dod_results`, `commands_run`, `verification_summary`, `files_changed`, `risks_or_deviations`, `handoff`, `learnings`, `decisions_made`
   - `pending_review`: `submitted_for_review` notes plus full submission shape
   - `failed`: `failure_reason` plus partial submission
   - `blocked`: `gated_on_job_id` (creates a `gated_on` edge — but cap-10 owns the auto-resolution; cap-5 just persists the field + the edge row)
   - Validation is shape-only (no test execution). Cap #5 is an "is the payload structurally valid?" check, not a "did the work actually pass?" check.

3. **Inline Decision/Learning creation.** `submit_job` parses `decisions_made[]` and `learnings[]` from the payload. Non-empty arrays → INSERT new rows in `decisions` / `learnings` tables, attached to the current Job (or, if the operator wants to attach to Pipeline/Project, that's a separate API field — design call). All in the same DB transaction as the Job state transition + audit row. Failure of any insert rolls back the whole submit.

4. **`review_complete` semantics.** Any actor (not just claimant) can call this. Final outcome is `done` or `failed` (NOT the full 4-outcome set — `pending_review` and `blocked` don't make sense as second-pass states). Audit row records the reviewing actor.

5. **Submission audit row shape.** `op='submit_job'`, `target_kind='job'`, `target_id=<job_id>`, `error_code` is conditional:
   - Success path with outcome=`done`: error_code=NULL
   - Success path with outcome=`failed`: error_code MIGHT be `failed` per Decision 21's diagnostic-code pattern, OR error_code stays NULL and outcome lives in `response_payload`. **Open design call.** Recommend: error_code=NULL on all success paths, outcome encoded in response_payload. Reserve error_code for actual denial paths.
   - Denial paths (Pydantic 422, claimant mismatch 403, state mismatch 409) → error_code per locked enum.

6. **State machine completeness check.** Cap-5 introduces `done`/`failed`/`blocked`/`pending_review` as reachable terminal-or-terminal-ish states. Verify the state CHECK constraint on `jobs` already permits all 4 (cap-3 set it up; should already include them — confirm). Verify `update_job`'s rejection set still excludes state writes (cap-3 lock). Verify `cancel_job` interaction with `pending_review` (can you cancel a pending-review Job? — design call; recommend yes since `cancel_job` permits any non-terminal state).

7. **Forward-compat for cap #9 inheritance.** When cap-5 ships the `decisions` / `learnings` tables, `get_job` / `get_pipeline` / `get_project` should start populating their existing-empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays. **Open design call:** does cap-5 wire the lookups, or does cap-9? Plan-update-2026-04-28-graph.md Section 2 says cap-9 wires them. Cap-5 just needs the tables to exist; cap-9 fills the response shape. **Recommend: stick with the plan — cap-5 ships tables + inline-create-at-submit, cap-9 ships the inheritance lookups.**

8. **`gated_on_job_id` on `submit_job(outcome=blocked)`.** This creates a `gated_on(self, gated_on_job_id)` edge in `job_edges`. Cap-10 owns the auto-resolution (when the gated_on Job becomes `done`, the blocked Job auto-promotes from `blocked` → `ready` if all its gates resolve). Cap-5 just inserts the edge row + sets `state='blocked'`. Validate that `gated_on_job_id` references a real Job in the same project — 404 if not.

9. **Test count + structure.** Cap-5 will likely add ~20-25 new tests (similar to cap-4's per-story counts). Use `_isolated_schema.py` for D&L test fixtures. Plus a dedicated `submit_job` test file per outcome + a `review_complete` test file + parity + atomicity. Estimate: 7 stories with C1 after `submit_job(done)` (most tested path) and C2 after evidence pack.

**Pre-existing cap-5 gap tickets:** **None.** Clean slate. (Cap-4 had AQ2-16 + AQ2-17 pre-existing; cap-5 starts fresh.)

---

## 9. The MCP setup is live across all four agents (unchanged from cap-4)

You can call AQ2 ops directly from your tool surface. The MCP server is registered in:
- Claude Code CLI (`~/.claude/settings.json`)
- Claude Desktop (`~/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/...`)
- Antigravity (Mario's Gemini IDE) (`~/.gemini/antigravity/mcp_config.json`)
- Codex CLI (`~/.codex/config.toml`)

All four point at `http://localhost:8001/mcp/` with Bearer auth. Use this during audits — call `mcp__agenticqueue__claim_next_job`, `mcp__agenticqueue__release_job`, `mcp__agenticqueue__heartbeat_job`, etc. directly. Cap-5 will add `submit_job` and `review_complete` to this list.

There are now 33 MCP tools live (was 29 pre-cap-4; +4 from cap-4: `claim_next_job`, `release_job`, `reset_claim`, `heartbeat_job`). Cap-5 will add 2 more, taking the count to 35.

**Note on MCP tool registry caching:** the tool list your session sees was populated at session-start. If cap-5 stories add new MCP tools, you may need to restart your session OR query `ToolSearch` to discover them mid-cap. This bit me on cap-4 — see "MCP tool registry refresh" carry-forward in the AQ2-65/AQ2-66 audit comments.

---

## 10. Memory rules (saved across this session — read these)

These live at `C:\Users\devop\.claude\projects\D--mmmmm\memory\` and load at session start. Honor them:

1. **`feedback_validate_codex_instructions`** — never write Codex handoff instructions from memory; validate worktrees, SHAs, ticket states, Docker, files BEFORE pasting. Reason: 2026-04-27 incident where I sent Codex a path that no longer existed.
2. **`feedback_validate_paths_in_tickets`** — validate file paths, script names, module references, DB constraint names, and CLI naming against the current repo BEFORE publishing Plane ticket bodies. Codex's pre-claim audits caught 7 bugs from fabricated references.
3. **`feedback_verify_never_trust`** — validate agent self-reports against actual code/tests; never sign off from payload text alone.
4. **`feedback_never_docker_down_v`** — `docker compose down -v` is forbidden. Named volumes hold data.
5. **`feedback_plane_is_truth`** — Plane is THE work queue.
6. **`feedback_adr_aq_030_story_format`** — every new Plane story uses ADR-AQ-030 bounded-fields shape; rewrite non-conforming bodies before they ship.

The full index is in `MEMORY.md`. Read it on session start.

---

## 11. Design ticket worth knowing — AQ2-60

**`[Tooling] Design AQ2 cross-agent process-memory delivery (Ghost + claude collab)`** — still `backlog`.

Where in the AQ2 data model do project-shared agent rules live? Options include MCP server `instructions` (cap-4 shipped this), per-tool descriptions, Project-level Learnings (cap-5 territory), and Job Contract DoD items.

**This ticket is now MORE relevant for cap-5** because cap-5 ships Learning nodes attached to Projects. Once Learning attachment-as-scope is real, the AQ2-60 design memo can land — Project-attached Learnings are a natural home for "rules every agent following work in this Project should know." Output is a memo at `plans/v2-rebuild/aq2-system-prompt-design.md`. Mario + claude work it together. **Not in cap-5 critical path** — but worth pinging Mario about it during cap-5 planning so the cap-5 D&L design accommodates it.

---

## 12. Backlog tickets to know about

After cap-4 + AQ2-70 wraps, the AQ2 backlog includes:

- **AQ2-60** — cross-agent process-memory design (Mario+claude collab; not blocking; might surface in cap-5 if D&L design needs it)
- **AQ2-72** — `claim_job(job_id)` direct-claim op + state-enumeration coverage matrix. **Important for cap-5 forward-compat:** cap-5 ships `state='blocked'` + `state='pending_review'` via `submit_job`, which AQ2-72's DoD-CLAIMID-03c/03d explicitly cover via manual SQL state-flip. When cap-5 ships, AQ2-72's plan should be amended to use the natural `submit_job(outcome=blocked)` and `submit_job(outcome=pending_review)` transitions instead of SQL — that's noted in AQ2-72's risks_or_deviations carry-forward.
- ~~AQ2-16, AQ2-17, AQ2-70, AQ2-71~~ — all closed; cap-4 + test-hygiene wrap-up.

Check `mcp__plane-bridge__plane_list_work_items project=AQ2` for the full current state when you start.

---

## 13. Capability planning workflow (from AGENTS.md Rule 12)

For non-trivial work like a new capability, use the three-gate planning cycle:

1. **Brief** — what + why. One paragraph. Mario approves.
2. **Pre-plan** — explore the codebase + draft a high-level approach. Use the `Explore` subagent type. Mario approves.
3. **Plan + Execute** — write the full plan (`capability-05-plan.md`), file the epic + child stories in Plane in ADR-AQ-030 format, hand to Codex.

For cap #5 specifically:
- Write `plans/v2-rebuild/capability-05-plan.md` with the same shape as `capability-04-plan.md` (read that file as the gold reference). Include locked decisions, story breakdown, and DoD items.
- File the cap-5 epic in Plane.
- File N child stories (probably 6-8 for cap-5 — schema + Pydantic models + submit per outcome + review_complete + Decision/Learning inline-create + parity/atomicity/C2).
- Run the plan past Mario AND Gemini for independent audit before Codex starts. Cap-4's plan rev cycle caught 5 P1 + P2 issues before code; cap-5 will likely need at least one rev cycle.

---

## 14. What "ready to plan" looks like

You're ready to plan cap #5 when you can answer all of these without re-reading the docs:

- What's the four-surface pact?
- What's the ADR-AQ-030 ticket format?
- Who does git? Who does state transitions? Who does merges (post cap-4 precedent)?
- What does cap-3.5's inline `contract` JSONB column mean for submit validation?
- What state transitions are valid for `submit_job`? For `review_complete`?
- What's the `audited_op` four-path semantics? When does cap-5 use `skip_success_audit=True` vs not?
- How does Decision 4 (D&L change shape) constrain cap-5's tables?
- What MCP richness pattern does cap-5 inherit from cap-4?
- How do you write a Verification block for a story?
- How do you redact evidence before commit?
- Where is the audit_log shape locked?
- What's an APPROVED audit comment supposed to contain?
- What's the `_isolated_schema.py` fixture pattern and why does cap-5 need it?

If any of these are blurry, re-read the relevant section above before planning.

---

## 15. First three things to do when you start

1. **Read `capabilities.md` (rev 4) end-to-end.** Especially section "Capability #5". It's about 35 lines.
2. **Read `capability-04-plan.md`** (820 lines, 22 Locked Decisions). It's the model for what your cap-5 plan should look like — same shape, same level of rigor. Skim if you've read it before; full read if not.
3. **Read `ADR-AQ-030-agent-ready-contract-checklist.md`** in full. The cap-5 submission validation IS this contract structure. You can't plan cap-5 without knowing it cold.

Then run `git -C D:/mmmmm/mmmmm-aq2.0 fetch origin main && git log origin/main --oneline -10` to confirm `main` is at `7f65654` (post-AQ2-70 tip). Then call `mcp__plane-bridge__plane_get_work_item issue_id="AQ2-62"` to read the closed cap-4 epic — it's your reference for what a complete capability epic looks like in Plane after C2.

Then start the cap-5 brief.

---

## 16. The thing nobody tells you (carried forward from cap-4)

Mario is sharp, fast, and direct. He'll tell you when you're wrong. He won't soft-pedal. Don't take it personally — it makes the work cleaner.

Three habits that will save you (lessons learned from cap-4):

- **Always validate before pasting.** Memory rule #1 + #2. If you're handing Codex an instruction that references a file or a state, run a tool call to check it FIRST. Cap-4's CI test failure happened because cap-4's Story 4.5 added an `app.py` import I didn't catch in my audit.
- **Be honest about uncertainty.** "I don't know yet, let me check" is the right answer. Speculation is the wrong answer. There's a memory rule for this too.
- **Don't pad summaries.** When you finish a story or capability, name what changed and what's next. One paragraph. Mario reads diffs and audit logs; he doesn't need a recap of his own decisions.
- **Cap-4 added: include a "module imports cleanly without DB envs" smoke in your audit checklist.** CI runs `uv run pytest` natively, not in Docker. If a story touches `app.py`'s top-level imports, verify CI's `test` job stays green. Cap-4 caught this at PR-time, not at per-story audit time.
- **Cap-4 added: for tests that mutate `actors` / `audit_log` / shared cap-4 tables, USE `apps/api/tests/_isolated_schema.py`.** The fixture-leakage trap (44 deactivated `aq-system-sweeper` rows) is a known anti-pattern; don't repeat it. Cap-5's D&L tests will be heavily tempted to commit to dev DB — resist.
- **Cap-4 added: spike fragile API assumptions BEFORE wiring production code.** Locked Decision 22 in cap-04-plan.md (FastMCP `set_instructions(...)` vs `instructions=` constructor) caught a real plan-vs-reality mismatch via a tiny first-commit spike test. Cap-5 may have similar surprises (e.g., the exact shape of inline Decision/Learning creation in `submit_job`'s same-tx path). Pre-spike where you can.

---

## 17. Cap-4 lessons folded into cap-5 expectations

Five things cap-4 taught us that cap-5 should bake in from the start:

1. **CI test-job env-vars matter.** Cap-4 tests ran fine locally in Docker but broke CI because the `test` workflow runs `uv run pytest` natively without `DATABASE_URL` etc. set. Story 4.5's `from aq_api._db import SessionLocal` at `app.py:16` triggered the failure chain at module-import time. **For cap-5: any new top-level import in `app.py` or main entrypoints is suspect. Lazy-import within functions where possible, or update `.github/workflows/test.yml` env vars to match Docker compose.**

2. **`setup` op vs reserved actors.** Cap-4 added the `aq-system-sweeper` actor. `setup`'s `_actors_exist` check naively returned `true` on a fresh DB-with-just-sweeper, blocking founder mint. Fix: `setup` now ignores the reserved sweeper. **For cap-5: if you add any new system-bootstrap rows (e.g., a "review_complete-bot" actor), update `setup`'s ignore-list immediately, AND add a "fresh DB plus this row → setup still works" test.**

3. **Auth session lifetime + concurrent claimers.** Cap-4's 50-claimer race test exposed that `current_actor` held the auth DB session for the full request lifetime, doubling DB connections. Cap-5's race tests on `submit_job` will hit the same pool if you don't preserve cap-4's `_auth.py` short-lived auth session pattern. **For cap-5: don't regress this. The `current_actor` dependency now opens its own short-lived session via `async with SessionLocal() as session` and releases the connection before route work begins.**

4. **MCP richness pattern is locked.** Don't relitigate. Use `FastMCP(MCP_NAME, tasks=False, instructions=MCP_INSTRUCTIONS)` constructor. Set `{destructiveHint, readOnlyHint, idempotentHint}` on every cap-5 mutation tool. Multi-part response for `submit_job` (Job + audit-row reference + next-step text). Description text mentions when to use vs what to call next.

5. **Per-story claude audits + Mario delegation pattern.** Cap-4 had 7 stories, 7 per-story claude APPROVEDs, 1 C1 (Mario delegated to claude), 1 C2 (Mario delegated to claude), 1 merge (Mario delegated to claude — "act as me"), 1 follow-up (AQ2-70) audited + merged by claude on Mario's "B" call. Cap-5 will likely follow the same pattern. Don't pre-empt merges, but expect to do the squash-merge yourself if Mario re-delegates.

---

## 18. What cap-5 unblocks

After cap-5 ships:
- **Cap #6** (Mario dogfoods one ticket end-to-end) — the riskiest assumption from the Brief. Cap-6 needs a closed loop (claim → work → submit), and cap-5 closes that loop. **Cap-6 is the first capability that actually exercises real work through AQ.** Get cap-5 right or cap-6's "does this work for real" check is meaningless.
- **Cap #7** (`list_runs` + `get_run` over audit_log) — cap-5's submit rows are the data that cap-7 queries.
- **Cap #8** (Context Packet) — claim already returns the stub Packet; cap-5's submit needs to know whether the Packet shape is enough or needs extending.
- **Cap #9** (Decisions + Learnings standalone ops + inheritance lookups) — cap-9 builds on the D&L tables cap-5 ships. Cap-9 wires `get_job`/`get_pipeline`/`get_project` to populate the D&L arrays cap-3 already returns as empty.

---

Good luck. Cap #5 closes the loop. Make it boring.

— claude (Opus 4.7), 2026-04-29
