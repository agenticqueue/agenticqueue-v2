# Capabilities — AgenticQueue 2.0 (v2-rebuild)

Status: Approved — Capability #1 plan ratified (Plane epic AQ2-1, stories AQ2-3..AQ2-11 in `backlog`). Flips to in-progress when AQ2-3 transitions to `todo` and the executor claims it.
Effort: v2-rebuild
Brief: [brief.md](brief.md)
Lexicon: [ADR-AQ-019](../../../mmmmm-agenticqueue/adrs/ADR-AQ-019-lexicon.md)
Contract structure: [ADR-AQ-030](../../../mmmmm-agenticqueue/adrs/ADR-AQ-030-agent-ready-contract-checklist.md)

---

## Architecture locks (the Pact)

These constraints govern every capability below.

### Auth model
- **API key = Actor identity.** Each key has a name; audit log attributes mutations to that name. No capability table, no `admin`/`supervisor`/`approve` permissions.
- **API keys are minted in the UI by a human only.** No `create_api_key` on CLI / MCP / REST.
- **Claim-binding** (data integrity, not permission): `release_job` and `submit_job` accept only the claimant. `reset_claim` is recovery — any key, requires reason, audit-logged.
- **First-run bootstrap**: `aq setup` (host-local CLI) creates the first Actor + first session before any key exists.
- **AQ 2.0 v1 is a trusted single-instance coordination tool.** API keys identify Actors for audit, not authorization. Not safe for multi-tenant shared services.

### Job lifecycle
States: `draft → ready → in_progress → done | failed | blocked | pending_review | cancelled`.

`update_job` is metadata-only. Transitions use explicit ops: `claim_next_job`, `release_job`, `submit_job` (4 outcomes), `reset_claim`, `review_complete`, `cancel_job`.

### Two entity types
- **Workflow** — versioned static template. Steps, no Jobs. Updates create new versions.
- **Pipeline** — dynamic execution. Ad-hoc or `instantiate`d from a Workflow (snapshots `instantiated_from_version`). Contains Jobs.

### Job edges (4 types)
- `gated_on(A, B)` — A blocked from `ready` until B is `done`. Auto-resolves: when B is `done`, AQ re-evaluates Jobs gated on B; A transitions to `ready` only when **all** gates are `done` AND its Contract is complete.
- `parent_of(A, B)` — hierarchy
- `sequence_next(A, B)` — ordering only
- `instantiated_from(A, B)` — Pipeline Job A cloned from Workflow step B

### Contract Profiles
Per ADR-AQ-030 (verbatim): bounded fields, DoD items declare verification_method/evidence_required/acceptance_threshold, `dod_results[]` maps every required DoD id to a terminal status with evidence.

v1 seeded profiles: `coding-task`, `bug-fix`, `docs-task`, `research-decision`.

### Customization line
| Customizable per Project (governed, versioned) | System invariant (fixed) |
|---|---|
| Contract Profiles, custom fields, labels, Workflows | Lifecycle states, edge types, submission payload shape, audit log shape, API key model, the four UI views |

### Stack
- Single repo `mmmmm-aq2.0/`
- Python: FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + Typer + FastMCP
- DB: Postgres with separate entity tables + a typed `edges` table
- Frontend: Next.js + Tailwind + shadcn/ui (read-only except 1 identity op: `create_api_key`)
- MCP mounted at `/mcp` in the same FastAPI process

---

## Capability list (ordered, dogfood at #6)

1. `[ ]` Four-surface ping — one canonical operation contract round-trips through REST + CLI + MCP + UI (plan ratified; awaiting execution start)
2. `[ ]` Actor identity, Bearer auth, and same-transaction audit log
3. `[ ]` Project, Workflow, Pipeline, and Job entities exist with full CRUD; one seeded static Workflow template ships
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

**Status:** `[ ]`

---

### Capability #2: Actor identity, Bearer auth, and same-transaction audit log

**Statement:** Authenticated Actors created during first-run setup can identify themselves, list other Actors, revoke their own API keys, and read the audit log; every mutation commits its domain change and audit row in one DB transaction.

**Why this is here:** The auth and audit foundation. Codex correction #7 (sharper auth language) and #8 (audit same-transaction) demand this be solid before any workgraph mutation. Per the locked Pact, API keys are the only identity model and audit attribution is the only "permission" enforcement.

**Depends on:** #1 (the four surfaces must exist).

**Scope guardrails (NOT in this capability):**
- No `create_api_key` on CLI/MCP/REST — that's UI-only and lives in capability #11.
- No domain entities yet (those land in #3).
- No claim binding logic (that's #4).
- The audit log is a queryable table with one row per mutation; it does not yet contain workgraph mutations because no workgraph ops exist.
- No `rotate_own_key` — users do `create_api_key` (UI) plus `revoke_api_key` (any surface, own key only) separately.

**Implements ops:**
- `setup` — `POST /setup` and `aq setup`; first-run only; creates the first Actor and a host-local API key. Disabled after first successful run.
- `whoami` — `GET /actors/me`, `aq whoami`, MCP `get_self`
- `create_actor` — `POST /actors`, `aq actor create`, MCP `create_actor`
- `list_actors` — `GET /actors`, `aq actor list`, MCP `list_actors`
- `revoke_api_key` — `DELETE /api-keys/{id}`, `aq key revoke`, MCP `revoke_api_key`. CLI/MCP/REST: own key only. UI variant ships in capability #11 with broader scope.
- `query_audit_log` — `GET /audit?actor=...&op=...&since=...`, `aq audit`, MCP `query_audit_log`

**Validation summary:** Run `aq setup` against an empty DB; the bootstrap returns an actor and a key. Hit `aq whoami` with the key — returns the Actor row. Create a second Actor; revoke the first key; `whoami` with the revoked key returns 401. Query the audit log — every mutation above appears with the correct `actor_id` and timestamp. Force a transactional failure (mock SQL error after the domain insert but before the audit insert) — confirm both rows roll back together.

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

Workflow (versioned static templates):
- `create_workflow` — `POST /workflows`, `aq workflow create`, MCP `create_workflow`
- `list_workflows` — `GET /workflows`, `aq workflow list`, MCP `list_workflows`
- `get_workflow` — `GET /workflows/{id}` (returns steps + step-edges + version), `aq workflow get`, MCP `get_workflow`
- `update_workflow` — `PATCH /workflows/{id}` (creates a new version; old version frozen), `aq workflow update`, MCP `update_workflow`
- `archive_workflow` — `POST /workflows/{id}/archive`, `aq workflow archive`, MCP `archive_workflow`

Pipeline (dynamic execution):
- `create_pipeline` — `POST /pipelines` (ad-hoc), `aq pipeline create`, MCP `create_pipeline`
- `instantiate_pipeline` — `POST /pipelines/from-workflow/{wf_id}` (snapshots Workflow version, copies steps as Jobs in `draft`), `aq pipeline instantiate`, MCP `instantiate_pipeline`
- `list_pipelines` — `GET /pipelines`, `aq pipeline list`, MCP `list_pipelines`
- `get_pipeline` — `GET /pipelines/{id}`, `aq pipeline get`, MCP `get_pipeline`
- `update_pipeline` — `PATCH /pipelines/{id}`, `aq pipeline update`, MCP `update_pipeline`

Job (CRUD only — no claim/submit yet):
- `create_job` — `POST /jobs` (binds to a Pipeline + Contract Profile), `aq job create`, MCP `create_job`
- `list_jobs` — `GET /jobs`, `aq job list`, MCP `list_jobs`
- `get_job` — `GET /jobs/{id}`, `aq job get`, MCP `get_job`
- `update_job` — `PATCH /jobs/{id}` (metadata only: title, description, label attachments; rejects state writes), `aq job update`, MCP `update_job`
- `comment_on_job` — `POST /jobs/{id}/comments`, `aq job comment`, MCP `comment_on_job`
- `cancel_job` — `POST /jobs/{id}/cancel`, `aq job cancel`, MCP `cancel_job`

Contract Profile discovery (read-only):
- `list_contract_profiles` — `GET /profiles`, `aq profile list`, MCP `list_contract_profiles`
- `describe_contract_profile` — `GET /profiles/{name}` (returns the four ADR-AQ-030 field groups), `aq profile get`, MCP `describe_contract_profile`

Plus: a database migration that seeds the v1 Contract Profiles (`coding-task`, `bug-fix`, `docs-task`, `research-decision`) and one static Workflow template (`ship-a-thing` with three steps).

**Validation summary:** Create a Project, attach two labels, register a custom label, create a Workflow with three steps, instantiate a Pipeline from it (verify three Jobs in `draft`, each with `instantiated_from` set and the Workflow's version snapshotted), create a fourth ad-hoc Job in the Pipeline with the `coding-task` Contract Profile, list everything, update the Job's title, comment on it, cancel one of the seeded Jobs. Every mutation appears in the audit log.

**Status:** `[ ]`

---

### Capability #4: A Job can be claimed atomically

**Statement:** Two Actors race to claim the same `ready` Job; exactly one wins (Job transitions to `in_progress` with the claimant set), the other gets a 409 Conflict; claimants can release; any Actor can `reset_claim` a stuck `in_progress` Job with a reason.

**Why this is here:** Proves "pull, do not push" (LinkedIn Post 10) is actually atomic at the database level. Without this, two agents can step on the same work and the loop is broken. Codex correction #3 (split `release_job` / `reset_claim`) is validated here.

**Depends on:** #3 (Jobs must exist before they can be claimed).

**Scope guardrails (NOT in this capability):**
- No `submit_job` — claim works, but the only way to exit `in_progress` here is `release_job` or `reset_claim`. Submit ships in #5.
- No `gated_on` resolution — claim works on any `ready` Job; the gating logic that promotes `draft → ready` lands in #10. For now, Jobs go directly to `ready` on creation.
- No claim filtering by `required_capabilities` — there are no capabilities. Claim is FIFO over `ready` Jobs (optionally filtered by Project).

**Implements ops:**
- `claim_next_job` — `POST /jobs/claim`, `aq claim`, MCP `claim_next_job`. Uses `SELECT ... FOR UPDATE SKIP LOCKED` semantics in a single transaction that also inserts the audit row.
- `release_job` — `POST /jobs/{id}/release`, `aq release`, MCP `release_job`. Claimant only. Job returns to `ready`.
- `reset_claim` — `POST /jobs/{id}/reset-claim` with required `reason`, `aq job reset-claim`, MCP `reset_claim`. Any key. Job returns to `ready`.

**Validation summary:** Create a Project, Pipeline, two `ready` Jobs. Two CLI clients (different keys) call `aq claim` simultaneously on the same Project; assert one gets a Job ID and the other gets the second Job (or null if there's only one). Re-run with one Job — exactly one client gets it, the other gets `None`. From the winner, call `aq release` — Job returns to `ready`. Re-claim, then from a different key call `aq job reset-claim --reason "claimant crashed"` — Job returns to `ready` and the audit log shows the reason. Run the race 50× to confirm no double-claim.

**Status:** `[ ]`

---

### Capability #5: A Job can be submitted with one of four outcomes, validated against its Contract, and the state machine + audit log advance correctly

**Statement:** A claimant calls `submit_job` with `outcome ∈ {done, pending_review, failed, blocked}` and a structured payload; AQ runs JSON Schema validation against the Job's Contract Profile (per ADR-AQ-030), checks state transition validity, commits the new state and audit row in one transaction, and emits a Run Ledger entry. Pending-review Jobs can be transitioned to `done` or `failed` by any key via `review_complete`. Contract Profile authoring (`register_contract_profile`, `version_contract_profile`) is governed and ships here so submit validation has profiles to validate against.

**Why this is here:** The submission boundary is moat #5 — Contracts are schemas, not CI. Codex correction #4 (`update_job` is metadata only — transitions are explicit ops) is validated by this capability. Without this, a Job can be created and claimed but never finished — the loop doesn't close.

**Depends on:** #4 (claim must exist before submit).

**Scope guardrails (NOT in this capability):**
- No DoD-runner that *executes* tests. Validation is shape-only: JSON Schema validation, required-evidence-fields-present, well-formed artifact pointers, declared-DoD-ids match `dod_results[]` ids, terminal status per DoD item.
- No `gated_on` auto-resolution — `done` updates the state but does not trigger downstream Job promotion. That's #10.
- No automatic Learning capture on submit — Learnings are manual and ship in #9.
- No Run Ledger query (just emit) — query lands in #7.
- Custom-field add/extend on profiles is deferred to v1.1; for v1, profiles are immutable once registered except for whole-version bumps.

**Implements ops:**
- `submit_job` — `POST /jobs/{id}/submit` with `outcome` and payload, `aq submit`, MCP `submit_job`. Claimant only. Outcome-specific required fields:
  - `done` — full ADR-AQ-030 submission (`dod_results`, `commands_run`, `verification_summary`, `files_changed`, `risks_or_deviations`, `handoff`, `learnings`)
  - `pending_review` — `submitted_for_review` notes plus the same submission shape
  - `failed` — `failure_reason` plus partial submission
  - `blocked` — `gated_on_job_id` (creates the `gated_on` edge in capability #10's machinery; here we just persist the field)
- `review_complete` — `POST /jobs/{id}/review-complete` with `final_outcome ∈ {done, failed}`, `aq review-complete`, MCP `review_complete`. Any key. Only valid when Job is in `pending_review`.
- `register_contract_profile` — `POST /profiles`, `aq profile register`, MCP `register_contract_profile`. Validates against ADR-AQ-030 minimum_claimable_invariants before activation.
- `version_contract_profile` — `POST /profiles/{name}/versions`, `aq profile bump`, MCP `version_contract_profile`. Existing claimed Jobs frozen on their version.

**Validation summary:** Register a custom Contract Profile (or use seeded `coding-task`). Create a Job, claim it, submit with `outcome=done` and a complete payload — Job transitions to `done`, Run Ledger has an entry, audit row written. Submit a different Job with an invalid payload (missing required field) — submit returns 422 with the schema error, Job stays in `in_progress`. Submit one with `outcome=pending_review` — Job lands in `pending_review`. From a different key, call `review_complete --final-outcome done` — Job is `done`. Submit one with `outcome=failed` — Job is `failed`. Submit one with `outcome=blocked` and `gated_on_job_id=<other_id>` — Job is `blocked` and the gated-on field is recorded (the auto-resolution wiring lands in #10). Try to submit a `done` Job again — rejected as terminal. Try to submit as a non-claimant — 403.

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

### Capability #7: Every claim and submit appends a Run Ledger entry queryable through all surfaces

**Statement:** Each `claim_next_job` and each terminal transition (submit / review_complete / cancel) appends an immutable row to the `run_ledger` table; the ledger is queryable by Job, by Actor, by time range, and by outcome through all four surfaces.

**Why this is here:** The Run Ledger is what makes AQ's audit story visible — not just to the audit log (which captures every mutation) but to a coordinator who wants to ask "what did Claude do this week?" Codex correction #8 (audit same-transaction) is reused here.

**Depends on:** #5 (submit must work before the ledger has anything to record).

**Scope guardrails (NOT in this capability):**
- The ledger is read-only after creation. No edit, no delete, no soft-delete.
- No analytics, no rollup queries, no aggregation. Ledger queries return rows.
- No filtering by Workflow version yet — that's a downstream nice-to-have.

**Implements ops:**
- `list_runs` — `GET /runs?job=...&actor=...&since=...&outcome=...`, `aq run list`, MCP `list_runs`
- `get_run` — `GET /runs/{id}`, `aq run get`, MCP `get_run`

Plus: claim and submit handlers (already in #4, #5) are extended to write a `run_ledger` row in their commit transactions.

**Validation summary:** Run the full claim → submit cycle on three Jobs. Query `aq run list --since yesterday` — three rows. Query `aq run list --actor claude-runner-1` — only that actor's runs. Query `aq run list --outcome done` — only `done` outcomes. Try `aq run list --job <id>` — full claim+submit ledger for that Job. Try to mutate a run row directly (DB hack) — schema-level append-only constraint blocks it.

**Status:** `[ ]`

---

### Capability #8: Claiming a Job returns a link-only Context Packet

**Statement:** A successful `claim_next_job` returns a Context Packet object containing pointers (IDs, not content): `project_id`, `pipeline_id`, `previous_jobs[]` (last 2 in the Pipeline's Sequence), `current_job_id`, `next_job_id`, `contract_profile_name`, `contract_id`. The same packet is reachable post-claim via `get_packet`. The Actor follows links to read what it needs via existing `get_*` ops.

**Why this is here:** The packet is *navigation, not content*. Codex correction (link-only design) and the user's spec ("read pj-1, pl-1, prev 2 jobs, current, next") are implemented here. AQ 1.0's Phase 3 Context Compiler built a content-bundling packet that became its own bottleneck; AQ 2.0 deliberately doesn't.

**Depends on:** #4 (claim must exist), #5 (Contracts must exist), #3 (entities must be linkable).

**Scope guardrails (NOT in this capability):**
- No content bundling. The packet does not include the Project description, Workstream goal, prior-job summaries, or Contract field text. The Actor follows links.
- No retrieval, no embeddings, no FTS. Graph traversal only — and only along the Pipeline's Sequence edges.
- No automatic redaction (since there's no content to redact).
- The packet does not include Decisions or Learnings yet — those are added as link references in #10's edge-aware variant, after the graph edges are real.

**Implements ops:**
- `get_packet` — `GET /jobs/{id}/packet`, `aq packet`, MCP `get_packet`. Returns the link-only navigation object. `claim_next_job` (already in #4) is extended to include the same packet in its response payload so the Actor doesn't need a second round-trip.

**Validation summary:** Instantiate a Pipeline with three Jobs in a Sequence (Job A, B, C). Claim Job B. The claim response contains a packet pointing to Project, Pipeline, Job A as the only previous, Job B as current, Job C as next, plus Contract Profile name and Contract ID. From the same key, call `aq packet B` — same payload. Confirm no Project description, Workflow goal text, or Job descriptions are in the packet — only IDs and stable identifiers. Have an agent (Claude Code) follow each link via `get_*` ops and verify it can reconstruct the full context independently.

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

**Status:** `[ ]`

---

### Capability #10: Jobs connect through typed edges; `gated_on` auto-resolves

**Statement:** Four edge types are persisted and queryable: `gated_on`, `parent_of`, `sequence_next`, `instantiated_from`. When a Job transitions to `done`, AQ re-evaluates all Jobs with unsatisfied `gated_on(_, that_job)` and transitions them from `draft` to `ready` if and only if all their `gated_on` dependencies are `done` and their Contract is complete. `instantiated_from` edges are created automatically by `instantiate_pipeline` (already in #3) and are surfaced by the new `list_job_edges` op here.

**Why this is here:** Codex pushback: "we have to be able to connect Jobs." Without typed edges and auto-resolution, AQ is just a queue with linked-list ordering. This capability makes Workstreams a real graph and makes dependency unblocking automatic.

**Depends on:** #3 (Jobs must exist), #5 (state transitions must be wired so `done` triggers the resolver), #9 (edge ops are general; Decisions also use them).

**Scope guardrails (NOT in this capability):**
- No additional edge types beyond the four. Custom edge types are not user-customizable per the locked customization line.
- No graph visualization view. UI views ship in #11 and don't include graph viz.
- No multi-hop dependency analysis tools. Single-hop resolution only.
- The `gated_on` resolver is synchronous within the `submit_job` transaction for now. If that becomes a bottleneck (it shouldn't for v1), we move it to a background worker — but that's out of v1 scope.

**Implements ops:**
- `link_jobs` — `POST /edges` with `{source_id, target_id, edge_type}`, `aq edge link`, MCP `link_jobs`
- `unlink_jobs` — `DELETE /edges/{source}/{target}/{type}`, `aq edge unlink`, MCP `unlink_jobs`
- `list_job_edges` — `GET /jobs/{id}/edges?direction=in|out|both`, `aq job edges`, MCP `list_job_edges`

Plus: the `submit_job` handler from #5 is extended with the gated-on resolver — when a Job transitions to `done`, run a query for every Job with an unsatisfied `gated_on` edge to it; for each, check whether all gates are satisfied AND the Contract is complete (per ADR-AQ-030 minimum_claimable_invariants); if both, transition `draft → ready` in the same transaction.

**Validation summary:** Create three Jobs A, B, C. Link `gated_on(B, A)` and `gated_on(C, A)`. Confirm B and C are in `draft`. Submit A with `outcome=done` — confirm B and C now in `ready`. Repeat with B incomplete-Contract — submit A → only C transitions to `ready`, B stays `draft` because its Contract is incomplete. Verify `instantiated_from` edges exist on Pipeline Jobs from instantiate_pipeline. List edges on a Job in both directions. Try to link a self-edge — rejected. Try `parent_of` on Jobs in different Projects — allowed (cross-project parent is rare but valid).

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

## Coverage check (every op covered exactly once)

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
| `get_project` | #3 |
| `update_project` | #3 |
| `archive_project` | #3 |
| `register_label` | #3 |
| `attach_label` | #3 |
| `detach_label` | #3 |
| `create_workflow` | #3 |
| `list_workflows` | #3 |
| `get_workflow` | #3 |
| `update_workflow` | #3 |
| `archive_workflow` | #3 |
| `create_pipeline` | #3 |
| `instantiate_pipeline` | #3 |
| `list_pipelines` | #3 |
| `get_pipeline` | #3 |
| `update_pipeline` | #3 |
| `create_job` | #3 |
| `list_jobs` | #3 |
| `get_job` | #3 |
| `update_job` | #3 |
| `comment_on_job` | #3 |
| `cancel_job` | #3 |
| `list_contract_profiles` | #3 |
| `describe_contract_profile` | #3 |
| `claim_next_job` | #4 |
| `release_job` | #4 |
| `reset_claim` | #4 |
| `submit_job` | #5 |
| `review_complete` | #5 |
| `register_contract_profile` | #5 |
| `version_contract_profile` | #5 |
| `list_runs` | #7 |
| `get_run` | #7 |
| `get_packet` | #8 |
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
| `create_api_key` | #11 (UI only) |

**Op count: 56.** (3 system + 5 identity/keys + 5 project + 3 labels + 5 workflow + 5 pipeline + 9 job lifecycle + 1 reset_claim + 1 review_complete + 3 edges + 4 contract profile + 4 decision + 4 learning + 2 run ledger + 1 packet + 1 audit. Capability #1 covers 2 ops; #2 covers 6; #3 covers 26; #4 covers 3; #5 covers 4; #6 covers 0 new (dogfood-only); #7 covers 2; #8 covers 1; #9 covers 8; #10 covers 3; #11 covers 1; #12 covers 0 new. Sum: 2+6+26+3+4+0+2+1+8+3+1+0 = 56. ✓)

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
| **Custom field add/extend on Contract Profiles** | Cap #5 | v1 profiles are immutable once registered except for whole-version bumps. No incremental field add. | v1.1 — `version_contract_profile` already exists; add a `patch_contract_profile` op for additive-only changes. |
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

When something else gets deferred during execution, add a row here with the same shape (Source / Reason / Proposed landing).

---

## Log

- 2026-04-26 — Pre-plan approved by Ghost. Capability #1 marked `[ACTIVE]`. Plane epic AQ2-1 + stories AQ2-3..AQ2-11 created. capability-01-plan.md drafted with ADR-AQ-030-shaped DoDs.
- 2026-04-26 — Codex review pass: timestamp parity loosened to "valid + recent" (not byte-equal); MCP transports clarified per ADR-AQ-021 (stdio via `aq-mcp` + streamable HTTP at `/mcp`; SSE deferred); `gen:types` reads committed OpenAPI snapshot (not live HTTP); validation script split into `.sh` + `.ps1`; Stories 1.8/1.9 swapped (Parity tests before CI workflows so CI references real test files); Web UI proxies to API via `app/api/health` + `app/api/version` route handlers (no CORS); scaffold expanded with lockfiles + workspace + framework configs.
- 2026-04-26 — Codex audit pass 2: (P1) folded ticket-body corrections into canonical bodies for AQ2-2..AQ2-11; (P1) added Plane `blocked_by` edges so dependency order is enforced by tooling not prose; (P1) fixed Story 1.7 Docker build context to repo root with `dockerfile:` paths and specified concrete healthcheck commands; (P2) reconciled `capabilities.md` cap-#1 status from `[ACTIVE]` back to `[ ]` until AQ2-3 transitions to `todo` (matches Plane truth).
