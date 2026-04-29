# Plan update — 2026-04-28 (graph addendum)

**Audience:** Codex (implementer), Claude (planner/auditor), Ghost (oversight).
**Scope:** Companion to `plan-update-2026-04-28.md` (same date). Adds Decision 7 — queryable graph in v1 — and the cap-level changes that follow. Read the primary plan-update first; this addendum builds on it.
**Authority:** This file supersedes conflicting sections of `capabilities.md` and the conflicting sections of cap #9 and cap #10 ticket bodies (when those tickets are written). Where they disagree, this file wins.
**Naming:** AQ stays AQ. Rename to Orxa is deferred per Ghost's standing call.

---

## TL;DR

One additional decision, added to the six in the primary plan-update:

7. **The graph is queryable in v1.** Three new traversal ops ship in cap #10. The inheritance chain is exposed in `get_job`/`get_pipeline`/`get_project` response shapes (no new ops, response extension only). The thesis name "work provenance graph" earns its verb.

Final v1 op count: **51 ops** (was 48 before this addendum).

---

## Decision 7 — Queryable graph in v1

### What changes

The graph in AQ becomes a query surface, not just a storage shape. Three concrete capabilities ship in v1:

1. **Inheritance traversal** — Decisions and Learnings attached up the chain (Job → Pipeline → Project) are reachable from any node, with the inheritance path explicit in the response. Implemented as a response-shape extension on existing read ops; no new ops.
2. **Transitive dependency closure** — Two new ops, `list_descendants` and `list_ancestors`, return the full set of nodes reachable from a starting node along specified edge types, with depth tracked per result.
3. **General neighborhood query** — One new op, `query_graph_neighborhood`, returns the local subgraph at depth N filtered by edge type, node type, and direction.

### Why

The thesis name "work provenance graph" requires the verb to do work. Without traversal, the graph is structure that exists but doesn't answer questions; the framing is oversold. Adding traversal makes "graph" honest:

- Agents can ask "what Decisions inform this Pipeline's work" in one call instead of walking the FK chain manually
- Coordinators can ask "what does this Job block transitively" before kicking off urgent work
- Cap #6 dogfood naturally exercises traversal — Mario will reach for these queries the moment he's running real work through AQ
- The launch story changes from "structured work coordination" to "queryable work provenance graph," which is substantively stronger and more defensible

### Why this is v1, not v1.1

Three reasons:

1. **The launch narrative depends on it.** "Work provenance graph" as a category-naming thesis needs traversal to be honest. v1 ships without it; the thesis gets cited as wrong.
2. **The cost is bounded.** Three ops, recursive CTEs, well-understood implementation pattern, known performance characteristics at v1 scale. Roughly the size of cap #3.5.
3. **Adding traversal later is harder than building it in.** Once agents have learned to walk the FK chain manually, retraining them on the right ops is friction. Get it right at v1.

### Scope guardrails (NOT in v1)

- No graph visualization UI. Stays in backlog (v1.1+).
- No materialized adjacency caches. Recursive CTEs only. If performance becomes a problem, materialization is a v1.1+ optimization.
- No path-finding (`find_path(node_a, node_b)`). Deferred to v1.1 unless cap #6 reveals a real use case.
- No graph-shaped permissions or per-edge auth. Edges are universally readable in v1 per the Pact's "every Actor sees every Job" rule.

---

## Section 1 — Op design (locked)

### Op 1: `list_descendants`

**Surface:** REST `GET /jobs/{id}/descendants?edge_types=gated_on&max_depth=10`, CLI `aq job descendants`, MCP `list_descendants`.

**Signature:**
```
list_descendants(
  node_id: UUID,
  node_type: Literal['job', 'pipeline', 'project', 'decision', 'learning'] = 'job',
  edge_types: list[EdgeType] = ['gated_on'],
  max_depth: int = 10  # server hard cap 50
) -> {
  nodes: list[{id, type, label, depth}],
  edges: list[{source_id, target_id, type, hop_index}],
  truncated: bool,           # true if max_depth was reached
  cycle_detected: bool       # true if a cycle was encountered
}
```

**Why these defaults:** `gated_on` is the only edge type with traversal value at v1 (Job dependency closure). Default depth 10 covers real engineering chains comfortably. Hard cap 50 prevents accidental DoS. Other edge types (`parent_of`, `sequence_next`, etc.) are parameterized so they're available without breaking change later.

**Cycle detection:** Recursive CTE uses depth tracking + visited set. Cycles in `gated_on` shouldn't exist (they'd mean A blocks B blocks A — undefined). If encountered, recursion stops at the cycle point and `cycle_detected: true` surfaces the bug instead of silently swallowing it.

**Audited:** No (read-only, matches cap #2 reads-not-audited lock).

**Annotations:** `readOnlyHint: true`.

### Op 2: `list_ancestors`

**Surface:** REST `GET /jobs/{id}/ancestors`, CLI `aq job ancestors`, MCP `list_ancestors`.

**Signature:** Mirror of `list_descendants` but traverses edges in reverse direction.

**Why both directions matter:** "What does this Job block?" (descendants) and "What blocks this Job?" (ancestors) are different questions. Both are real. Two ops with symmetric shape is clearer than one op with a `direction` flag.

**Audited:** No.

**Annotations:** `readOnlyHint: true`.

### Op 3: `query_graph_neighborhood`

**Surface:** REST `GET /graph/neighborhood?start_id=...&start_type=...&depth=...&edge_types=...&direction=...&node_type_filter=...`, CLI `aq graph neighborhood`, MCP `query_graph_neighborhood`.

**Signature:**
```
query_graph_neighborhood(
  start_node_id: UUID,
  start_node_type: Literal['job', 'pipeline', 'project', 'decision', 'learning'],
  depth: Literal[1, 2, 3] = 1,        # hard restricted to 1, 2, or 3
  edge_types: list[EdgeType] = [],    # empty = all types
  direction: Literal['in', 'out', 'both'] = 'both',
  node_type_filter: list[str] = []    # empty = all types
) -> {
  nodes: list[{id, type, label, depth}],
  edges: list[{source_id, target_id, type, hop_index}],
  truncated: bool
}
```

**Why depth is restricted to 1, 2, or 3:** Two reasons. First, deeper traversals over a specific edge type are better expressed as `list_descendants`/`list_ancestors`. Second, depth-N neighborhood over all edge types is exponential in branching factor — depth=4 with 5 average neighbors per node is up to 625 nodes. Not useful, just expensive. Cap it.

**Why `direction` matters:** "What does this Job reference" (outbound — Decisions cited, Jobs gated on) is a different question from "what references this Job" (inbound — Jobs blocked, Decisions mentioning it). Both are real questions; both need to be supported.

**Why `node_type_filter` exists:** "Show me everything within 2 hops" is rarely the actual question. "Show me Decisions within 2 hops" or "Show me Jobs within 2 hops" is the real query 90% of the time. The filter makes the common case efficient.

**Footgun protection:** Server returns `error_code='neighborhood_too_large'` with `count` field if the unfiltered result would exceed 500 nodes. Caller must refine via filters. This trades the rare "I really want all 800 nodes" case for protecting the common case.

**Audited:** No.

**Annotations:** `readOnlyHint: true`.

### Inheritance traversal — response shape extension (no new ops)

`get_job`, `get_pipeline`, `get_project` response shape extends from `{job: {...}}` to:

```
{
  job: {...},
  decisions: {
    direct: list[Decision],          # attached to this entity
    inherited: list[{
      decision_id: UUID,
      decision: Decision,            # full content inline
      inherited_from: 'pipeline' | 'project',
      inherited_from_id: UUID
    }]
  },
  learnings: { same shape }
}
```

**Why direct vs inherited matters:** Direct attachments are this entity's own provenance. Inherited are context. The agent should be able to tell them apart at a glance. A Decision attached to the parent Project is policy; a Decision attached to the parent Pipeline is workstream-specific. Without the path, they're indistinguishable.

**Why content inline (not just IDs):** This is the one place the link-only Context Packet philosophy bends. Decisions and Learnings are short structured content; they're already in memory when the entity is fetched (via the FK chain walk); requiring the agent to re-fetch each one with `get_decision` is wasteful indirection. The Context Packet (cap #8) stays link-only because it's about navigation; `get_job` is about reading the Job, and reading the Job means knowing what informs it.

**`get_pipeline`** returns direct attachments + inherited from Project.
**`get_project`** returns direct only (top of chain).
**`get_job`** returns direct + inherited from parent Pipeline + inherited from Project.

**Audited:** No (still reads).

**Implementation note:** Two extra SELECT statements per fetch (parent Pipeline's Decisions/Learnings, parent Project's Decisions/Learnings). Indexed lookups via `attached_to_id + attached_to_type` composite index. Negligible cost at v1 scale.

---

## Section 2 — Where this lands in the cap structure

Three changes to existing capabilities.

### Cap #9 changes (D&L ops + inheritance response shape)

Cap #9 already owns Decision and Learning ops. The inheritance response-shape extension lands here because it's a D&L concern, not a graph traversal concern. Specifically:

- `get_job` (currently in cap #3, AQ2-47) gains the inheritance fields when cap #9 ships. This is a forward-compatible response extension — cap #3's `get_job` returns `{job: {...}}`; after cap #9 ships, it returns the extended shape with empty arrays if no D&L exist.
- Same for `get_pipeline` (cap #3) and `get_project` (cap #3).

**Implementation pattern:** Cap #3 ships `get_job`/`get_pipeline`/`get_project` with the response shape that includes empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays. Cap #9 wires the actual D&L lookups into those arrays. This means cap #3 ships the final response shape from day one and cap #9 fills it in — no breaking change between the two caps.

**Cap #9 op count:** unchanged (still 8 ops: create_decision, list_decisions, get_decision, supersede_decision, submit_learning, list_learnings, get_learning, edit_learning).

### Cap #10 changes (edges + traversal)

Cap #10 expands from 3 ops to 6 ops. Locked decisions update:

| Was | Now |
|---|---|
| "No multi-hop dependency analysis tools. Single-hop resolution only." | **REMOVED.** Multi-hop traversal ships via `list_descendants` and `list_ancestors`. |
| "5 edge types (after cap #3.5)" | Stays. Edge types unchanged. Traversal ops query the existing edge surface. |
| "No graph visualization view. UI views ship in #11 and don't include graph viz." | Stays. Visualization stays in backlog (v1.1+). |
| "The `gated_on` resolver is synchronous within the `submit_job` transaction." | Stays. Resolver semantics unchanged. |

**Cap #10 op count:** 3 → 6.

New ops added: `list_descendants`, `list_ancestors`, `query_graph_neighborhood`.

**New scope guardrails:**
- No materialized adjacency caches in v1. Recursive CTEs over the live edges table.
- No `find_path(a, b)` op in v1. Add when there's a real use case.
- Cycle detection is mandatory in `list_descendants` and `list_ancestors`. The response surfaces `cycle_detected: true` when encountered; recursion stops at the cycle point and returns the partial result.
- Server hard cap on traversal depth: 50 for closure ops, 3 for neighborhood. These are sanity ceilings, not user-configurable.
- Server hard cap on neighborhood node count: 500. Returns `error_code='neighborhood_too_large'` if exceeded.

### Cap #8 changes (Context Packet — minor)

The Context Packet stays link-only. Decisions and Learnings continue to be returned as IDs, not content. The agent follows links to `get_decision` and `get_learning` if it wants content.

The packet does, however, gain the inheritance metadata — for each Decision/Learning ID returned, the packet indicates whether the attachment is direct (to the current Job) or inherited (from parent Pipeline or Project). Same `inherited_from` field as the response-shape extension.

**Why the difference between cap #8 (link-only) and cap #9 response shape (content inline):** The Context Packet is about navigation — give the agent enough structure to reconstruct context, but make the agent decide what to fetch. `get_job`/`get_pipeline`/`get_project` are about reading the entity itself; if you're reading the entity, you want its full provenance, not pointers to it. The two ops serve different cognitive purposes.

---

## Section 3 — Op count math

Pre-graph-decision count (after primary plan-update): 48 ops.

| Change | Ops added |
|---|---|
| Cap #10 — `list_descendants` | +1 |
| Cap #10 — `list_ancestors` | +1 |
| Cap #10 — `query_graph_neighborhood` | +1 |
| Cap #9 — inheritance response shape on `get_job`/`get_pipeline`/`get_project` | 0 (response extension, not new ops) |
| Cap #8 — packet inheritance metadata | 0 (response extension on `get_packet`, not new ops) |

**Final v1 op count: 51 ops.**

The regenerated op coverage table:

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
| `create_job` | #3 (takes inline `contract` JSONB per primary plan-update Decision 3) |
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
| `list_runs` | #7 (queries audit_log via partial index per primary plan-update Decision 1) |
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

**Total: 51 ops.**

---

## Section 4 — Capability spec deltas

### `capabilities.md` cap #8 description

Replace this passage:

> The packet does not include Decisions or Learnings yet — those are added as link references in #10's edge-aware variant, after the graph edges are real.

with:

> The packet returns structural pointers (ID lists) to Decisions and Learnings: those attached directly to the Job, those inherited from the parent Pipeline, and those inherited from the parent Project. Each pointer carries an `inherited_from` field with values `direct`, `pipeline`, or `project` so the agent can reason about scope. No relevance ranking. No content. The Actor follows links via `get_decision` and `get_learning` to retrieve content; the inheritance metadata exists so the agent knows where each attachment came from before deciding whether to fetch it.

### `capabilities.md` cap #9 description

Add this passage at the end of the cap description:

> When this capability ships, the response shapes of `get_job`, `get_pipeline`, and `get_project` (already in cap #3) are extended to include `decisions` and `learnings` objects with `direct: [...]` and `inherited: [...]` arrays. Direct entries are full Decision or Learning records attached to the entity; inherited entries are full records attached up the chain (Pipeline's parent Project for `get_pipeline`; both parent Pipeline and parent Project for `get_job`). Each inherited entry carries an `inherited_from` field (`pipeline` or `project`) and the source entity's ID so the agent can reason about scope. `get_project` returns only direct attachments; the Project is the top of the inheritance chain.

### `capabilities.md` cap #10 description

Replace the existing scope guardrails section with:

> **Scope guardrails (NOT in this capability):**
> - No additional edge types beyond the five (`gated_on`, `parent_of`, `sequence_next`, `job_references_decision`, `job_references_learning`). Custom edge types are not user-customizable per the locked customization line.
> - No materialized adjacency caches. Traversal queries hit the live `edges` table via Postgres recursive CTEs.
> - No `find_path(node_a, node_b)` op. Path-finding is deferred to v1.1+ unless cap #6 dogfood reveals a use case.
> - No graph visualization view. UI views ship in #11 and don't include graph viz; visualization is v1.1+.
> - No graph-shaped permissions. Edges are universally readable per the Pact's "every Actor sees every Job" rule.

Replace the existing implements ops list with:

> **Implements ops:**
>
> Edge persistence:
> - `link_jobs` — `POST /edges` with `{source_id, target_id, edge_type}`, `aq edge link`, MCP `link_jobs`
> - `unlink_jobs` — `DELETE /edges/{source}/{target}/{type}`, `aq edge unlink`, MCP `unlink_jobs`
> - `list_job_edges` — `GET /jobs/{id}/edges?direction=in|out|both`, `aq job edges`, MCP `list_job_edges`
>
> Graph traversal:
> - `list_descendants` — `GET /graph/{node_type}/{node_id}/descendants?edge_types=...&max_depth=...`, `aq graph descendants`, MCP `list_descendants`. Returns nodes + edges + `truncated` + `cycle_detected` flags.
> - `list_ancestors` — symmetric inverse of `list_descendants`. `GET /graph/{node_type}/{node_id}/ancestors`.
> - `query_graph_neighborhood` — `GET /graph/neighborhood?start_id=...&start_type=...&depth=...&edge_types=...&direction=...&node_type_filter=...`. Returns local subgraph at depth 1, 2, or 3. Hard cap 500 nodes; returns `error_code='neighborhood_too_large'` if exceeded.
>
> Plus: the `submit_job` handler from #5 is extended with the gated-on resolver — when a Job transitions to `done`, run a query for every Job with an unsatisfied `gated_on` edge to it; for each, check whether all gates are satisfied AND the Contract is complete (per ADR-AQ-030 minimum_claimable_invariants); if both, transition `draft → ready` in the same transaction. (Single-hop resolution at submit time stays unchanged; the new traversal ops query the same edge structure asynchronously without affecting the resolver.)

Update the validation summary to add the three new traversal validations:

> **Plus traversal checks:** Build a chain of 6 Jobs with `gated_on(B,A), gated_on(C,B), gated_on(D,C), gated_on(E,D), gated_on(F,E)`. Call `list_descendants(A)` — returns 5 nodes (B through F) with depth values 1 through 5; `truncated=false`, `cycle_detected=false`. Call `list_ancestors(F)` — returns 5 nodes (E through A). Force a cycle: `link_jobs(F, A, gated_on)` — this should fail at link time if cycle prevention is on `link_jobs` (recommended) OR succeed and surface as `cycle_detected=true` on subsequent traversals (acceptable). Call `query_graph_neighborhood(B, depth=2, edge_types=['gated_on'])` — returns 5 nodes within 2 hops in both directions. Call `query_graph_neighborhood(B, depth=3, node_type_filter=['decision'])` and verify only Decision nodes return. Call with `depth=3` against a deeply-connected node and force `neighborhood_too_large` — verify `error_code` and `count` field.

### `capabilities.md` cap #3 — `get_job`/`get_pipeline`/`get_project` response shape

Add to the cap #3 description:

> **Note:** `get_job`, `get_pipeline`, and `get_project` ship in cap #3 with the final response shape — including empty `decisions: {direct: [], inherited: []}` and `learnings: {direct: [], inherited: []}` arrays. Cap #3 returns empty arrays because Decisions and Learnings don't exist yet (they ship in cap #9). When cap #9 lands, those arrays start populating. This avoids a breaking response-shape change between cap #3 and cap #9. Cap #3 implementations of these three ops just emit the empty arrays unconditionally; cap #9 wires the inheritance lookup into them.

---

## Section 5 — New ticket spec for cap #10's expanded scope

When Ghost queues cap #10, the epic should reflect the expanded scope. Suggested ticket structure:

**Cap #10 epic — Edges and graph traversal**

Story-level breakdown (5 stories, mirrors cap #3.5's pattern):

- Story 10.1 — Edge persistence (link_jobs, unlink_jobs, list_job_edges)
- Story 10.2 — `gated_on` auto-resolution in submit_job (cap #5 extension)
- Story 10.3 — `list_descendants` and `list_ancestors` (closure ops)
- Story 10.4 — `query_graph_neighborhood` (general subgraph)
- Story 10.5 — Parity tests + cycle detection coverage + neighborhood-too-large coverage + C-checkpoint

Cycle detection spec (Story 10.3 + 10.4): The recursive CTE uses Postgres's standard cycle detection pattern — track visited node IDs in an array, terminate recursion at the cycle point, and surface `cycle_detected: true` in the response. We do NOT prevent cycles at `link_jobs` time in v1; cycles in `gated_on` are a real bug worth surfacing rather than rejecting silently. (Cycle prevention at link time is a backlog item if it becomes a real problem.)

Performance spec (Story 10.5): At v1 scale (hundreds of Jobs, tens of edges per Job), all traversal ops complete in <100ms. Validation requires building a Pipeline with 50 Jobs and a 10-deep `gated_on` chain, running each traversal op, and asserting <100ms. This is the canary that tells us when materialization becomes necessary.

---

## Section 6 — Backlog updates

Add these rows to `capabilities.md` Backlog:

| Item | Source | Reason for deferral | Proposed landing |
|---|---|---|---|
| Materialized adjacency caches | This addendum — Section 1 | Recursive CTEs handle v1 scale (hundreds of Jobs) in single-digit ms. Materialization is an optimization that's only justified once a real workload is hitting limits. | v1.1+ when traversal queries become a performance concern. |
| `find_path(node_a, node_b)` op | This addendum — Section 1 | Path-finding is powerful but unproven need at v1. Cap #6 dogfood will reveal whether it's wanted. | v1.1+ if dogfood reveals a use case. |
| Cycle prevention at `link_jobs` time | This addendum — Section 5 | v1 surfaces cycles as `cycle_detected: true` on traversal. Preventing them at link time is stricter but adds overhead to every link operation. | v1.2+ if cycles become a real operational problem. |
| Graph visualization UI | (already in backlog) | UI views ship in cap #11 read-only; graph viz is its own workstream needing layout-engine decision. | v1.1+ |

---

## Section 7 — What this commits the thesis to

The launch story changes substantively. Before Decision 7:

> "AQ is structured work coordination with audit trails. Every claim and submit is recorded. Decisions and Learnings live as graph nodes attached to the work that produced them. Pipelines describe the work; Jobs describe the units; the audit log describes what happened."

After Decision 7:

> "AQ is a queryable work provenance graph. Every claim, submit, and decision is a node or edge. You can ask 'what does this Job depend on transitively' or 'what Decisions inform this Pipeline's work' and get a structured answer in one call. The graph is the database of record; the audit log is its history; the domain tables are caches over both. The same five primitives — atomic claim, inline contracts, same-transaction audit, heartbeat lease, attached Decisions/Learnings — combine into a coordination plane that runs autonomously across multiple agents while keeping a complete, queryable record of every move."

That's a thesis worth citing. The graph carries weight, not just the audit log. The traversal capabilities prove it.

---

## Section 8 — Action items

### Codex (implementer)

When cap #10 starts (after cap #9 ships):

1. Read this addendum's Section 1 in full before claiming Story 10.1.
2. Story 10.3 (`list_descendants` and `list_ancestors`) requires recursive CTEs with cycle detection — read the Postgres docs on `WITH RECURSIVE` and the `CYCLE` clause if unfamiliar.
3. Story 10.4 (`query_graph_neighborhood`) is the most complex; the depth restriction (1, 2, or 3 only) is enforced at the Pydantic boundary AND at the SQL boundary as a sanity check.
4. Story 10.5 includes a performance canary — build the 50-Job, 10-deep dependency chain and assert all traversals complete in <100ms.

When cap #3 implements `get_job`/`get_pipeline`/`get_project`:

5. Ship the final response shape with empty `decisions` and `learnings` arrays. This is forward-compatible with cap #9.

When cap #9 ships:

6. Wire the inheritance lookups into the cap #3 ops' responses. No new ops; pure response-shape population.
7. The lookup is a SELECT against `decisions` and `learnings` tables filtered by `attached_to_id` and `attached_to_type`, plus a parent-walk via the entity's FK chain. Use a single CTE per entity to fetch direct + inherited in one round trip.

### Claude (planner/auditor)

1. When auditing cap #3's `get_job`/`get_pipeline`/`get_project` implementations, verify they emit the empty `decisions` and `learnings` arrays. If they don't, flag it — the response shape is part of the cap #3 contract even though the data isn't populated until cap #9.
2. When auditing cap #9, verify the inheritance lookups correctly distinguish `direct` from `inherited`, and that `inherited_from` and `inherited_from_id` are populated for every inherited entry.
3. When auditing cap #10's traversal ops, verify cycle detection works (build a test cycle and confirm `cycle_detected: true` surfaces), the depth caps are enforced server-side, and the neighborhood-too-large guard fires at >500 nodes.
4. From cap #6 forward, treat traversal as part of the contract. If a Decision-or-Learning-related submission references a manual FK walk instead of the traversal ops, flag it — the agents should be using the graph queries.

### Ghost (oversight)

1. Review this addendum. Flag anything misremembered or mis-decided.
2. **Decision needed:** when to publish `capabilities.md` rev 4. The primary plan-update recommended publishing immediately after committing it. With this addendum, rev 4 should fold both updates in one publication, not two.
3. **Action needed in repo:** commit this file at `D:\mmmmm\mmmmm-aq2.0\plans\v2-rebuild\plan-update-2026-04-28-graph.md` alongside the primary plan-update. Both files together form the authoritative direction for cap #3.5 forward.
4. **No Plane action required yet.** Cap #10 tickets don't exist; cap #9 tickets don't exist; cap #3 tickets that are already written don't reference traversal. The only Plane action is when those caps queue, the epic and story bodies should reference this addendum.

---

## Provenance

- This addendum reflects the Decision 7 made at the close of the 2026-04-28 strategy conversation.
- It builds on `plan-update-2026-04-28.md` (same date, primary update).
- Live state of cap #3 was validated against Plane via MCP on 2026-04-28; this addendum's cap #9 and cap #10 changes are forward-looking — those tickets don't exist yet.
- Authority: Ghost confirmed Decision 7 ("lean into the graph as queryable") at the close of the conversation.
- Companion artifact: `capabilities.md` rev 4 (to be produced) folds both plan-updates into the canonical spec. Until that is published, both plan-update files are authoritative on conflict.
- Filed under: `D:\mmmmm\mmmmm-aq2.0\plans\v2-rebuild\plan-update-2026-04-28-graph.md`.
