# Brief — AgenticQueue 2.0 (v2-rebuild)

Status: Approved
Date: 2026-04-26
Effort: v2-rebuild
Lexicon: [ADR-AQ-019](../../../mmmmm-agenticqueue/adrs/ADR-AQ-019-lexicon.md) — Project / Workstream / Job / Sub-task / Contract / Sequence / Actor / Run Ledger / Queue. Execution modes: static / dynamic / instantiation.

---

## One-line summary

AgenticQueue 2.0 is a shared work system that hands every Actor the right
context the moment they claim a Job, and only marks the Job done when their
structured proof matches the Contract.

## Customer

Priority order. Each layer adopts only if the layer above is real.

1. **Mario, dogfood-first.** AQ 2.0 is the work queue for the mmmmm projects
   (agenticqueue, trading, terminal, beauty, hitl-cf). Everything else is
   downstream of "does this work for me on real work."
2. **Solo builders / small ops** running 2–5 agents who got burned by RAG /
   Mem0 / "second brain" memory products. Self-host, technical, early-adopter.
3. **Engineering managers / tech leads** who want visibility into the *work*,
   not the agents (Post 20). Budget holders, need polish, want to know the
   work is moving safely.
4. **Open-source infrastructure builders** who care about coordination
   primitives. Contributors, not just consumers.

Mario can't sell solo builders unless AQ works for him; solo builders can't
sell their managers without trust; managers can't sell to OSS contributors
without an OSS-shaped product.

## Moat

AQ 2.0's moat is **architectural restraint enforced as product shape.** Five
hard No's, each defensible because it strengthens trust:

1. **No memory.** The graph is the context source. Records are born
   structured, not summarized prose.
2. **No content ingestion.** No repo scanning, no Slack / Confluence
   backfill, no historical reconstruction. Start clean from now.
3. **No agent runtime.** AQ doesn't host models, doesn't proxy tool calls,
   doesn't sit in the middle. BYO everything.
4. **No write UI.** Mutations through CLI / REST / MCP only. The UI is a
   read-only window into the graph, not a clickops surface.
5. **No verdict on the work.** AQ doesn't run tests, review code, or judge
   outcomes. It gates the *shape* of the return payload — Contracts are
   schemas, not CI. Right shape, Job moves; humans (or the next Job) judge
   the contents downstream.

Competitors can't match this without rebuilding their core. A memory product
can't say "we don't store memory." An agent framework can't say "we don't
run agents." A CI tool can't say "we don't judge work." The No's are
load-bearing.

## Competition

Six categories. AQ 2.0's stance against each:

1. **Memory products** (Mem0, RAG-as-a-service, vector-store wrappers,
   MemGPT). *Their pitch*: "agents need long-term memory." *AQ stance*
   (Posts 1, 7): the agent doesn't need memory; it needs the right context
   the moment it claims a Job. AQ stores nothing summarized.
2. **Agent frameworks** (LangGraph, AutoGen, CrewAI, agent SDKs,
   AutoGPT-style runtimes). *Their pitch*: "we run the agents and orchestrate
   the loop." *AQ stance* (Posts 4, 9, 14): AQ doesn't run agents. BYO
   runtime. AQ holds the work; agents are external.
3. **Human-PM tools** (Jira, Linear, Plane, Asana, Trello). *Their pitch*:
   "ticket tracking for teams." *AQ stance* (Posts 5, 11, 14): same surface,
   wrong shape. Built for humans, leaks human assumptions into agent work.
   AQ uses an agent-first lexicon, not borrowed PM vocabulary.
4. **Spec-driven tools** (spec-driven dev, design-by-spec, structured-prompt
   frameworks). *Their pitch*: "specs make agents reliable." *AQ stance*
   (Post 18): a spec is still text. AQ uses Contracts because the *return*
   must satisfy the schema, not just the input.
5. **LLM wrappers / coding agents** (Cursor, Devin, Replit Agent, Sweep,
   hosted Aider). *Their pitch*: "smarter coding agent in your editor /
   cloud." *AQ stance* (Post 14): AQ is not an agent. It's the work system
   the agent claims from. Cursor and Devin can both be Actors against AQ.
6. **Karpathy / Obsidian wiki approach** (compiled knowledge,
   generated-from-notes systems). *Their pitch*: "compress prose into
   cleaner prose." *AQ stance* (Post 7): better exhaust is still exhaust.
   AQ starts structured, not summarized.

**Closest comparison risk**: human-PM tools. Outsiders will see Project /
Workstream / Job and ask "isn't this just Linear with extra steps?" The
answer (Post 5 framing — *"Jira plus Kafka for agents"*) needs the demo to
land. Without the demo, the comparison hurts. Defuse it early in
positioning.

## Success criteria

The full product as articulated in the LinkedIn posts is running in
production. Specifically:

- At least one Project with a Workstream and a Sequence of Jobs.
- Multiple kinds of Actors (Claude Code, Codex, a human, a cron script) each
  successfully claim a Job, do it, and return a Contract-valid payload.
- Every Context Packet, Contract, Decision, and Learning lives in the graph
  and is queryable through all four surfaces (API, CLI, MCP, read-only UI).
- A fresh Actor picks up a downstream Job and acts correctly using only the
  context the graph hands it — no need to read the repo or the chat history.
- Mario uses AQ 2.0 as the work queue for a real Project (dogfooding).
- Repo is open source with a clean install path.

**How we get there: capability-first.** No big bang. Each capability
validated end-to-end before the next is started. The full product is the
destination; the capability list is the road.

## Riskiest assumption

The plan for AQ 1.0 was right; the build sprawled. Too many features got
layered before the core loop ever worked. AQ 2.0 only stays small if we hold
the line — validate one capability end-to-end before starting the next. If
we skip ahead and build three things in parallel "to save time," AQ 2.0
collapses into AQ 1.0 with new nouns.

## Source documents

- `mmmmm-ghost\agenticqueue_linkedin_posts.md` — canonical product voice
  (Posts 1–20, Captions A–F).
- `mmmmm-ghost\agenticqueue_linkedin_post_assessment.md` — authority lane and
  publishing order.
- `mmmmm-agenticqueue\adrs\ADR-AQ-019-lexicon.md` — canonical ontology.
- `D:\mmmmm\AGENTS.md` Rule 12 — capability-first planning cycle.
- `D:\mmmmm\docs\planning\how-to-write-a-brief.md` — this Brief follows that
  template.
