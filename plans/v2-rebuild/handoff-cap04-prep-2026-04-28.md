# Handoff to next opus — Cap #4 planning prep

Filed 2026-04-28 by claude (Opus 4.7) after cap #3 + cap #3.5 received Mario approval. This is your first read. Treat it as a session-start orientation. Read it once end-to-end, then keep it open as a reference while you plan cap #4.

You have **zero context**. The doc below is structured to give you exactly what you need to plan cap #4 without re-deriving everything from scratch.

---

## 1. What AQ2 is, in one paragraph

AQ2 (AgenticQueue 2.0) is a single-instance work coordination tool for AI agents. A human (Mario, "Ghost") creates a Project, defines work as Pipelines containing Jobs, and agents (Claude / Codex / Gemini) claim Jobs, do the work, and submit results. AQ2 enforces a contract on every Job — a JSON checklist of "what does done look like?" — and keeps an immutable audit log of every mutation. The whole system is exposed on **four surfaces** that must serve byte-equal payloads: REST, CLI, MCP, and a read-only Web UI. This four-surface byte-equality is the foundational invariant; CI mechanically enforces it with parity tests.

---

## 2. Status as of 2026-04-28

**Done:**
- Cap #1 (four-surface ping) — merged 2026-04-26
- Cap #2 (Authenticated Actors + Bearer auth + same-transaction audit log) — merged 2026-04-27
- Cap #3 (Project, Pipeline, Job entities with full CRUD, seeded `ship-a-thing` template) — merged today
- Cap #3.5 (schema consolidation: dropped Workflow + Contract Profile tables, added inline `contract` JSONB on jobs, added clone_pipeline + archive_pipeline) — merged today

**Today's branch state:**
- `aq2-cap-03` is at `69db0d4`, AWAITING the squash-merge PR. Codex opens that PR after I post this handoff. Once merged, cap-3 + cap-3.5 land on `main`. AQ2-39 epic auto-closes.

**Next epic:** Cap #4 — atomic claim. **You plan it.**

---

## 3. Where things live

| Thing | Path |
|---|---|
| Code repo | `D:\mmmmm\mmmmm-aq2.0\` |
| Capability map (canonical spec) | `plans/v2-rebuild/capabilities.md` (rev 4 as of today) |
| Cap-3 plan (history + rev-4 banner) | `plans/v2-rebuild/capability-03-plan.md` |
| Cap-2 plan | `plans/v2-rebuild/capability-02-plan.md` |
| Cap-1 plan | `plans/v2-rebuild/capability-01-plan.md` |
| Plan-update authority docs (override capabilities.md) | `plans/v2-rebuild/plan-update-2026-04-28.md` and `plans/v2-rebuild/plan-update-2026-04-28-graph.md` |
| ADR (Architectural Decision Records) | `D:\mmmmm\mmmmm-agenticqueue\adrs\` |
| ADR-AQ-030 (the contract-checklist ADR) | `D:\mmmmm\mmmmm-agenticqueue\adrs\ADR-AQ-030-agent-ready-contract-checklist.md` |
| ADR-AQ-019 (the lexicon — defines every term) | `D:\mmmmm\mmmmm-agenticqueue\adrs\ADR-AQ-019-lexicon.md` |
| Per-story evidence | `plans/v2-rebuild/artifacts/cap-NN/<story-evidence>.{txt,xml,md}` |
| Cap-3.5 evidence | `plans/v2-rebuild/artifacts/cap-0305/` |
| AGENTS.md (process rules) | `D:\mmmmm\AGENTS.md` |
| CLAUDE.md (claude-specific rules) | `D:\mmmmm\CLAUDE.md` |

**Plane** is the work-tracking system: `http://localhost:8502/mmmmm/`. Use `/browse/<TICKET-KEY>/` for direct ticket links. AQ2 is project `AQ2`. Tickets are filed there with the standardized ADR-AQ-030 shape (see Section 6).

---

## 4. The agents in the loop

Three agents do work, plus Mario as gate:

| Agent | Role |
|---|---|
| **Mario / Ghost** | Sole human operator. Approves at gates. Source of truth on scope decisions. |
| **claude (you)** | Plans, audits, files tickets. Does NOT touch git on `aq2-cap-NN` branches. Read-only on repo + Plane state transitions during audits. |
| **Codex** | Implementer. Does ALL git operations (commits, pushes). Moves Plane tickets to `done` after claude APPROVED. |
| **Gemini** | Independent auditor for plan rev cycles. Used for fresh-eye reviews on capability plans before they ship. |
| **Sonnet** | QA / mechanical sweeps. Used for tickets that are "execute this list and capture evidence" type work. |

This separation of concerns is real and standing. Don't change it without Mario's say-so.

---

## 5. The standing process — read this carefully

**Per-story flow:**

1. claude writes the ticket (ADR-AQ-030 shape — see Section 6).
2. Codex claims via Plane (`plane_update_status` to `in progress` + `agent:codex` label — note: `plane_claim_next` doesn't work; the Plane project uses `todo` not `queued`, so manual transition is the standing workaround).
3. Codex implements + pushes + posts a closeout JSON comment with `dod_results[]`.
4. claude audits live (re-runs verification commands from MY session, doesn't just trust evidence files; uses REST + MCP tool calls + DB queries).
5. claude posts an APPROVED or REJECTED comment.
6. Codex moves the ticket to `done` after seeing APPROVED. (claude does NOT move tickets — Codex owns state transitions.)
7. Codex claims the next ticket.

**Capability gates:**
- C1 (mid-capability checkpoint): Codex stops, claude audits, Mario approves before resuming.
- C2 (end-of-capability checkpoint): same. Plus this is where the capability PR opens.
- C3 (PR merge): Mario merge approval. Codex squash-merges. NO self-merge.

**Hard rules:**
- mypy `--strict` + ruff + pytest must all be clean on EVERY push. Lint regression caught at audit blocks the commit until fixed.
- Validate live, don't trust evidence files alone. Mario flagged this multiple times: "verify, never trust." If you write a Plane ticket that references a file path or constraint name, validate against the live repo BEFORE publishing.
- Never run `docker compose down -v`. Never. Use isolated test DBs for fresh-install scenarios.
- Never commit secrets. Founder Bearer keys go in Vault at `secret/aq2/founder-key`. Configs read from env vars or Vault, never inline.

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

- Bullet list of cap-2 / cap-3 / cap-N locks that this ticket honors or extends.

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

## 7. The plan-update-2026-04-28 docs

These two files override `capabilities.md` and `capability-03-plan.md` on conflict:

- **`plan-update-2026-04-28.md`** (Mario, 2026-04-28) — six structural decisions:
  1. Run Ledger collapses into audit_log (no separate table)
  2. Workflows collapse into Pipelines (`is_template` flag + `clone_pipeline` op)
  3. Contract Profiles dropped entirely (inline `contract JSONB` on each Job)
  4. `submit_job` Contract requires `decisions_made[]` + `learnings[]` arrays
  5. Webhooks deferred to v1.1 (pull-only via MCP polling in v1)
  6. Read-only UI forever (single exception: `create_api_key` UI mint flow in cap #11)
- **`plan-update-2026-04-28-graph.md`** (Mario, 2026-04-28) — one decision (Decision 7):
  7. The graph becomes queryable. Cap #10 ships three traversal ops: `list_descendants`, `list_ancestors`, `query_graph_neighborhood`. `get_job` / `get_pipeline` / `get_project` ship with `decisions: {direct, inherited}` + `learnings: {direct, inherited}` arrays for cap #9 forward-compat (cap #3 already shipped these as empty arrays).

These docs are your AUTHORITY when capabilities.md text is stale. Capabilities.md was updated to rev 4 to reflect both, but if you find a conflict, the plan-update files win.

---

## 8. Cap #4 — what's locked and what you plan

**Verbatim from `capabilities.md`:**

> ### Capability #4: A Job can be claimed atomically
>
> **Statement:** Two Actors race to claim the same Job; exactly one wins via Postgres advisory lock + state-CAS update; loser receives a clean `409`. Heartbeat lease: claimed Jobs require periodic `heartbeat_job` calls; stale claims are recoverable via `reset_claim`.
>
> **Why this is here:** Without atomic claim, AQ is a recipe for race conditions on shared queues. This capability makes "exactly one Actor working a Job at a time" a system property, not a discipline.
>
> **Depends on:** #3 (Jobs must exist before they can be claimed).
>
> **Implements ops:**
> - `claim_next_job` — atomic claim with FIFO + label filter + project scope
> - `release_job` — voluntary release back to `ready`
> - `reset_claim` — admin op for stuck claims (heartbeat timeout)
> - `heartbeat_job` — claim lease renewal

**Locked decisions inherited from earlier caps that constrain cap #4:**

- **F-P0-1 (cap-03 lock):** No path in cap #1 / cap #2 / cap #3 / cap #3.5 creates Jobs in `state='draft'`. The `draft` state is reserved for cap #10's `gated_on` mechanism. Cap #4's `claim_next_job` does NOT operate on `draft` — it claims `ready` only.
- **`is_template` filter (cap-03.5 lock):** `claim_next_job` MUST exclude Jobs in template Pipelines (`pipelines.is_template = true`) AND archived Pipelines (`pipelines.archived_at IS NOT NULL`). Mirror what `list_ready_jobs` does in `apps/api/src/aq_api/services/list_ready_jobs.py:29-77`.
- **`project_id` REQUIRED:** `claim_next_job(project_id, label_filter)` — `project_id` is non-optional, same as `list_ready_jobs`. No global cross-project claim.
- **State machine:** `ready → in_progress` is cap #4's own transition. `in_progress → ready` (via `release_job` or `reset_claim`) too. Cap #4 does NOT transition to `done` / `failed` — that's cap #5's `submit_job`.
- **Audit:** every claim, release, reset, heartbeat is audited per cap #2's same-transaction guarantee.
- **HMAC lookup_id (cap-02 lock):** authentication continues to use HMAC-SHA256 lookup_id; no changes here.
- **F-P1-rev2-7 unwound (cap-3.5):** the composite FK `(pipeline_id, project_id)` was dropped in cap #3.5. Cap #4's claim query JOINs `jobs` → `pipelines` for `is_template/archived_at` filtering anyway, so this isn't a regression — but be aware that `jobs.project_id` is denormalized at create-time and consistency is enforced at the application layer in `clone_pipeline` + `create_job`, not at the DB level.
- **`heartbeat_job` is a NEW op** in the rev-4 capability count. Cap-4 ops total: 4 (claim, release, reset, heartbeat).

**Pre-existing cap-4 gap tickets** (file your plan around these):
- **AQ2-16**: `[edge] Claim orphaning — heartbeat + timeout sweeper for stuck in_progress jobs`
- **AQ2-17**: `[edge] Covering index for claim query — idx_jobs_claim(project_id, state, created_at)` — note: cap #3 already added a partial btree `idx_jobs_state_project_created` partial WHERE state='ready'. Verify whether cap #4 needs a NEW index or extends the existing one.

**Concrete things to design:**
- Postgres advisory-lock pattern: per-project lock key derived from `project_id` + label filter hash, OR per-row `FOR UPDATE SKIP LOCKED` on the picked row. Choose one and lock it in the plan.
- Heartbeat cadence: agents must call `heartbeat_job` every N seconds; after M seconds without heartbeat, the claim is "stale." Pick N and M.
- Reset semantics: `reset_claim` is admin-only OR self-only? Cap-2 actors don't have authorization tiers; figure out the access pattern.
- Submission shape for `claim_next_job` response: just the Job, OR Job + Context Packet (cap #8 ships the Packet but the shape may need to match here for forward-compat).

---

## 9. The MCP setup is live across all four agents

You can call AQ2 ops directly from your tool surface. The MCP server is registered in:
- Claude Code CLI (`~/.claude/settings.json`)
- Claude Desktop (`~/AppData/Local/Packages/Claude_pzs8sxrjxfjjc/...`)
- Antigravity (Mario's Gemini IDE) (`~/.gemini/antigravity/mcp_config.json`)
- Codex CLI (`~/.codex/config.toml`)

All four point at `http://localhost:8001/mcp/` with Bearer auth. Use this during audits — call `mcp__agenticqueue__list_jobs` etc. directly. That's the audit-discipline upgrade I made on AQ2-49: actually exercise the MCP path, don't just trust parity test snapshots.

There are 29 MCP tools live. You'll have access to all of them by name `mcp__agenticqueue__<op>`. Check ToolSearch when needed — they're deferred-loaded.

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

## 11. AQ2-60 — open design ticket worth knowing

**`[Tooling] Design AQ2 cross-agent process-memory delivery (Ghost + claude collab)`**

Filed during this session after observing that "rules every AQ2 agent should follow" (e.g., "validate paths before publishing tickets") currently live only in claude's private memory. Codex doesn't see them. Future agents won't either.

The ticket asks: where in the AQ2 data model do project-shared agent rules live? Options include MCP server `instructions`, per-tool descriptions, Project-level Learnings (cap #9), and Job Contract DoD items.

This is a **design ticket** — output is a memo at `plans/v2-rebuild/aq2-system-prompt-design.md`. Mario + claude work it together. It's NOT in the cap-4 critical path; you can ignore it during cap-4 planning. But understand: cap #9 (Decisions + Learnings) when it ships is the most likely landing point for the mechanism, and `get_job` / `get_pipeline` / `get_project` already ship with empty `decisions` + `learnings` inheritance arrays for cap #9 forward-compat.

---

## 12. Other open tickets to know about

After today's merges, the AQ2 backlog will include:
- **AQ2-16**: cap-4 claim orphaning (heartbeat + sweeper) — pre-existing edge gap; folds into your cap-4 plan
- **AQ2-17**: cap-4 covering index — same; verify whether cap #3's partial btree suffices
- **AQ2-60**: AQ2 cross-agent process-memory design (collab; not blocking cap-4)
- **AQ2-61**: three-surface QA sweep (DONE today; standalone branch `aq2-61` ready for separate PR)
- **AQ2-52**: Gemini MCP wiring (DONE)
- **AQ2-53**: dev DB wipe fix (DONE — folded into cap-3.5)

Check `plane_list_work_items` for the full current state when you start.

---

## 13. Capability planning workflow (from `AGENTS.md` Rule 12)

For non-trivial work like a new capability, use the three-gate planning cycle:

1. **Brief** — what + why. One paragraph. Mario approves.
2. **Pre-plan** — explore the codebase + draft a high-level approach. Use the `Explore` subagent type. Mario approves.
3. **Plan + Execute** — write the full plan (`capability-04-plan.md`), file the epic + child stories in Plane in ADR-AQ-030 format, hand to Codex.

For cap #4 specifically:
- Write `plans/v2-rebuild/capability-04-plan.md` with the same shape as `capability-03-plan.md` (read that file as the gold reference). Include locked decisions, story breakdown, and DoD items.
- File the cap-4 epic in Plane.
- File N child stories (probably 4-6 for cap-4 — claim/release/reset/heartbeat plus parity + atomicity tests + checkpoint).
- Run the plan past Mario AND Gemini for independent audit before Codex starts. Cap-3 had two audit cycles (rev-1 + rev-2) before code landed; cap-4 will likely need at least one rev cycle.

---

## 14. What "ready to plan" looks like

You're ready to plan cap #4 when you can answer all of these without re-reading the docs:

- What's the four-surface pact?
- What's the ADR-AQ-030 ticket format?
- Who does git? Who does state transitions?
- What does F-P0-1 lock?
- What's the difference between `is_template=true` Pipelines and runs?
- Why does `list_ready_jobs` need a JOIN to pipelines?
- What does cap #4's `claim_next_job` need to filter on?
- What's the heartbeat invariant cap #4 introduces?
- How do you write a Verification block for a story?
- How do you redact evidence before commit?
- Where is the audit_log shape locked?
- What's an APPROVED audit comment supposed to contain?

If any of these are blurry, re-read the relevant section above before planning.

---

## 15. First three things to do when you start

1. **Read `capabilities.md` (rev 4) end-to-end.** Especially section "Capability #4." It's about 30 lines.
2. **Read `capability-03-plan.md`** (rev 3 historical + rev 4 banner). It's the model for what your cap-4 plan should look like. ~700 lines but skim-able.
3. **Run `git log origin/main --oneline -10`** to see what's actually shipped. As of 2026-04-28 evening, expect cap #3 + cap #3.5 squash to land as a single commit on main once the PR merges.

Then call `mcp__plane-bridge__plane_get_work_item issue_id="AQ2-39"` to read the closed cap-3 epic — that's your reference for what a complete capability epic looks like in Plane.

Then start the cap-4 brief.

---

## 16. The thing nobody tells you

Mario is sharp, fast, and direct. He'll tell you when you're wrong. He won't soft-pedal. Don't take it personally — it makes the work cleaner.

Three habits that will save you:

- **Always validate before pasting.** Memory rule #1 + #2. If you're handing Codex an instruction that references a file or a state, run a tool call to check it FIRST.
- **Be honest about uncertainty.** "I don't know yet, let me check" is the right answer. Speculation is the wrong answer. There's a memory rule for this too.
- **Don't pad summaries.** When you finish a story or capability, name what changed and what's next. One paragraph. Mario reads diffs and audit logs; he doesn't need a recap of his own decisions.

---

Good luck. Cap #4 unblocks capabilities #5 (submit), #6 (dogfood), and the entire downstream agent-claims-and-works flow. It's foundational. Make it boring.

— claude (Opus 4.7), 2026-04-28
