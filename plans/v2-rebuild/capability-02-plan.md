# Plan: AQ 2.0 Capability #2 — Authenticated Actors + Bearer auth + same-transaction audit log

## Context

This plan covers **Capability #2** of AgenticQueue 2.0. Cap #1 (Four-surface ping) is **already merged to `main` at `96e158d`** on `agenticqueue/agenticqueue-v2`. Cap #2 is the auth + audit foundation that every later capability builds on.

Cap #2 statement (verbatim from `capabilities.md`):

> Authenticated Actors created during first-run setup can identify themselves, list other Actors, revoke their own API keys, and read the audit log; every mutation commits its domain change and audit row in one DB transaction.

This is rev 2 of the cap #2 plan, incorporating Codex's external audit (P0 + P1 findings), Gemini's technical edge cases, and Ghost's directive that **every story carry a human outcome — not just a code description**.

### Why cap #2 matters (the human outcome)

After cap #2 ships, AQ 2.0 stops being an unauthenticated hello-world toy. From this point forward:
- Every byte of state in AQ 2.0 attributes to a named Actor — a human, an agent, a script.
- "Who did this?" has an answer for every mutation, every time, forever.
- A leaked key can be killed. Mario stays in control of his own access.
- The same-transaction audit guarantee means we never see "an audit row for a change that didn't happen" or "a change with no audit fingerprint." Trust in the queue is mechanical, not aspirational.
- AQ 2.0 becomes safe enough to put real work through — caps #3–#5 can land on top.

---

## Hard preconditions (must be on `main` before cap #2 first commit)

Per Ghost's "no carrying forward known debt" rule. Verified state on `agenticqueue/agenticqueue-v2:main`:

| Ticket | Title | Status |
|---|---|---|
| AQ2-12 | Dependabot config | **MERGED** ✓ (`eaf1bac`) |
| AQ2-13 | CodeQL workflow | **MERGED** ✓ (`721e3e2`) |
| AQ2-14 | SECURITY.md + private vulnerability reporting | **MERGED** ✓ (`42766b7`) |
| AQ2-15 | gitleaks secret-scanning workflow | **branch open** (`aq2-15-gitleaks`) → must merge |
| AQ2-18 | Adapter auth-forwarding rules in capabilities.md | **must rewrite + merge** (the original ticket's "claude-mcp-bridge" model is the security hole Codex flagged; see §"AQ2-18 amendment" below) |

Cap #2 first commit on `aq2-cap-02` is gated on AQ2-15 + the rewritten AQ2-18 being on `main`.

---

## Codex P0 + P1 corrections folded in (audit pass on rev 1)

### P0-1 — MCP auth is now actually secure
**Original (broken)** model: MCP server has its own "claude-mcp-bridge" key; clients pass `agent_identity` opaquely. Anyone who could reach `/mcp` could mutate as bridge with self-asserted identity.
**Corrected** model: **MCP HTTP `/mcp` requires the caller's own Bearer token**, identical to REST. There is no bridge actor. `agent_identity` becomes a purely *informational* field captured in `audit_log.claimed_actor_identity` for the case where one identity (e.g. Claude Code) is acting on behalf of another (e.g. `claude-opus-4-7`). Authentication remains the caller's API key. MCP stdio binary (`aq-mcp`) reads `~/.aq/config.toml` and forwards the human's API key as Bearer to the FastAPI process.

### P0-2 — Identity actually crosses MCP→REST boundary
**Original (broken)**: MCP handlers delegated through `httpx` to REST. ContextVars don't cross HTTP.
**Corrected**: Cap #2 introduces an **in-process service layer** at `apps/api/src/aq_api/services/`. Both REST handlers AND MCP tool handlers call the same Python functions. No HTTP round-trip between MCP and REST. ContextVars propagate naturally because there's no boundary.

### P0-3 — Audit semantics, locked
- **Reads are NEVER audited.** `whoami`, `list_actors`, `query_audit_log`, `health_check`, `get_version` write zero audit rows. Cap statement says "every mutation"; reads aren't mutations.
- **Mutations always audit, including denials.** `create_actor` (success or 422), `revoke_api_key` (success or 403 forbidden), `setup` (special case — see P0-4) write an audit row. A 403 on cross-actor revoke commits an audit row with `error_code="forbidden"` AND no domain row. That's the audit-only-row case.
- **Unexpected exceptions (5xx)** roll back fully — no audit row, no domain row. Stack trace stays in app logs.
- **Validation errors (422 from Pydantic before the handler runs)** are not audited — the mutation never started.

### P0-4 — Setup bootstrap is auditless
**Locked**: `setup` writes ZERO audit rows. The founder + bridge Actor rows themselves are the bootstrap evidence (their `created_at` timestamps are the audit). Future audit queries that ask "what happened before any audit row?" get the empty set; that's setup. This drops the impossibility of attributing audit to an actor that doesn't yet exist.

### P0-5 — Secret leakage in artifacts, three-layer fix
1. **Audit redactor** is regex-recursive on field names. Pattern: `(?i)(^|_)(key|token|secret|password|hash)(_|$)`. Replaces value with `"[REDACTED]"`. Applies on every nested level of JSONB before insert.
2. **Artifact redactor** (`scripts/redact-evidence.sh`) runs on every file under `plans/v2-rebuild/artifacts/cap-02/` before any commit. Strips known plaintext-key patterns (argon2-encoded hashes, 32-char URL-safe tokens, base64 fragments matching `[A-Za-z0-9_-]{40,}`).
3. **gitleaks workflow (AQ2-15)** is the last line of defense. If a secret escapes the first two layers, gitleaks blocks the PR.

### P0-6 — Reconciliation with capabilities.md
- Cap #1 is **DONE** at `96e158d`. Removed the stale "awaiting merge" language.
- **Web in cap #2 is scoped down**: ships `/login` (paste-in form), `/logout`, and a `/whoami` panel. Drops the `/actors` Web view and the `/audit` Web view from rev 1. Reasons: capabilities.md cap #11 explicitly says "no audit-log browser. The audit log is queryable via CLI/MCP/REST only." The four read-only views (Pipelines, Workflows, ADRs, Learnings) are cap #11's, not cap #2's. Cap #2's Web addition is *auth scaffolding* — the minimum that lets a human prove they're logged in.
- **`create_actor` minting**: capabilities.md says "API keys are minted in the UI by a human only." This conflicts with cap #2's `create_actor` minting on REST/CLI/MCP. Reconciliation: cap #2 lets `create_actor` mint a key on the same endpoint, but the **plaintext key is returned ONCE in `CreateActorResponse`** and never re-fetchable. UI in cap #11 will be the "blessed" mint UX; cap #2 makes the lower-level op available because there's no UI yet to mint through. This is documented as a deviation per ADR-AQ-030 in submission `risks_or_deviations`.
- AQ2-18's auth-forwarding rules are **rewritten** before cap #2 starts (see §"AQ2-18 amendment" below).

### P1-7 — First-run race uses Postgres advisory lock
`setup` opens a transaction, takes `pg_advisory_xact_lock(hashtext('aq:setup-singleton'))`, then re-checks `EXISTS(SELECT 1 FROM actors)`. Concurrent setup attempts serialize on the lock and exactly one wins.

### P1-8 — Revoke-last-key race uses row-level locking
`revoke_api_key` does `SELECT ... FROM api_keys WHERE actor_id=$1 AND revoked_at IS NULL FOR UPDATE`, counts active keys, refuses if revoking would leave zero. Two concurrent revokes serialize.

### P1-9 — Web cookie environment switch
New env `AQ_COOKIE_SECURE` (default `false` for `localhost` dev; `true` behind a TLS-terminating reverse proxy). The compose `web` service reads it explicitly. iron-session config: `secure: process.env.AQ_COOKIE_SECURE === "true"`. No more `NODE_ENV` gating.

### P1-10 — CI/validation DB wiring fix
- DB host port stays unmapped (locked).
- Host-side pytest connects via the compose project network when run via `docker compose run --rm test-runner ...`. CI uses the same pattern; no host-port mapping needed.
- `AQ_MCP_BRIDGE_KEY` is **gone** (no bridge actor exists in the corrected model). The CI flow is: spin DB → run setup → capture founder key from setup response → export as `AQ_FOUNDER_KEY` for subsequent steps. Never a GH secret.
- `validate-cap02.sh` runs `docker compose down` (no `-v`) then `docker compose up -d --build --wait`, then explicitly resets the schema with `alembic downgrade base && alembic upgrade head` before running setup. Idempotent.

### P1-11 — Migrations run on app boot
`apps/api/Dockerfile` entrypoint runs `alembic -c apps/api/alembic.ini upgrade head` before `exec uvicorn ...`. Boot fails loud if migrations don't apply.

### P2-12 — Flaky DoDs replaced
- Argon2 timing test removed (the "within 50%" assertion is not a meaningful security property).
- `pg_dump -s` byte-equality replaced with structural assertion: parse `\d+` output and compare table+column+constraint sets, not raw text.
- CLI 401 byte-equality dropped from cap #2; the cap #1 CLI wraps upstream errors as `http_error` and that's correct behavior. CLI assertion is "exit code != 0 and stderr JSON contains `http_error` for 4xx upstream."

### Gemini's edge cases folded in
- **argon2 memory_cost is in KiB**: `PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)` for 64 MiB.
- **All FastAPI routes and FastMCP tool handlers are `async def`** — never sync. Documented as KISS guardrail.
- **Audit JSONB bloat**: cap #2 redacts secrets but does NOT cap payload size. Capping is a cap #3+ concern (recommended truncator at 50KB once domain payloads start landing). Filed in Backlog.
- **`AQ_SESSION_SECRET` injection**: explicit env mapping in `docker-compose.yml` web service.

---

## AQ2-18 amendment (lands BEFORE cap #2 first commit)

The original AQ2-18 ticket described a flawed "claude-mcp-bridge" model. Codex's audit caught it. AQ2-18 is rewritten to:

**Five corrected auth-forwarding rules:**

1. **REST is the only place that resolves API key → Actor.** Other surfaces forward the key opaquely.
2. **CLI** reads `AQ_API_KEY` env or `~/.aq/config.toml`, sends `Authorization: Bearer <key>`, never inspects the key.
3. **Web** server-side route handlers read the iron-session cookie, look up the per-session API key (stored encrypted in cookie payload), forward as Bearer to FastAPI. Browser never sees plaintext.
4. **MCP HTTP `/mcp`** requires the caller's own Bearer. Same auth as REST. There is no bridge actor.
5. **MCP stdio** (`aq-mcp` binary) reads `~/.aq/config.toml` for the operator's API key and forwards as Bearer to the local FastAPI process.

**`agent_identity` is informational only.** Optional field on every MCP tool's input schema. When provided, populates `audit_log.claimed_actor_identity` for the audit trail; never affects authentication. Required only when an MCP host (e.g. Claude Code) is calling on behalf of a different identity (e.g. `claude-opus-4-7`).

**Audit log columns:**
- `authenticated_actor_id` UUID NOT NULL — always the API key bearer's actor.
- `claimed_actor_identity` TEXT NULL — populated from MCP `agent_identity` when present; NULL otherwise.

This rewrite + capabilities.md auth-model clarifications (Web auth scaffolding allowed in cap #2) ship as a single PR before cap #2 begins.

---

## Locked decisions for cap #2

1. **Postgres 16-alpine** in compose. Internal-only network. Named volume `aq2_pg_data`. `pg_isready` healthcheck.
2. **SQLAlchemy 2.x async (asyncpg) at runtime; psycopg sync for Alembic.**
3. **Argon2id, work factors `time_cost=2, memory_cost=65536 (KiB = 64 MiB), parallelism=2`.** Reject bcrypt.
4. **Application-side hashing** (`key_hash` is plain TEXT). pgcrypto enabled only for `gen_random_uuid()`.
5. **iron-session for Web cookie session.** `AQ_COOKIE_SECURE` env-driven. Reject NextAuth.
6. **In-process service layer.** Both REST and MCP handlers call the same Python service functions. No HTTP delegation between surfaces inside the API process.
7. **MCP HTTP requires caller's Bearer.** No bridge actor. `agent_identity` is decorative-only.
8. **Setup is auditless.** `setup` writes Actors and keys; no `audit_log` row.
9. **Reads are not audited.** Audit is mutation-only, including denials with `error_code` set.
10. **Web in cap #2 ships `/login`, `/logout`, and a `/whoami` panel only.** No `/actors`, no `/audit` Web views — those stay CLI/MCP/REST per cap #11's scope guardrails.
11. **All API/MCP handlers are `async def`.** Never sync. ContextVar propagation across asyncio tasks is then guaranteed.
12. **Real-stack validation discipline carries forward.** `docker compose down && up -d --build --wait` + `alembic downgrade base && upgrade head` + `_assert_commit_matches_head()` invariant.
13. **Strict ADR-AQ-030 evidence per story.** Every artifact under `plans/v2-rebuild/artifacts/cap-02/`, redacted before commit.

---

## Stack additions

### Python (`apps/api/pyproject.toml`)
```
sqlalchemy[asyncio] >=2.0.30,<2.1
asyncpg            >=0.29,<0.30
psycopg[binary]    >=3.1.18,<3.2     # alembic-only
alembic            >=1.13,<1.14
argon2-cffi        >=23.1,<24
pydantic-settings  >=2.2,<3
```

### Web (`apps/web/package.json`)
```
iron-session  ^8.0.0
```

### Compose additions
```yaml
db:
  image: postgres:16-alpine
  environment:
    POSTGRES_USER: aq
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}     # required, no default
    POSTGRES_DB: aq2
  volumes: [aq2_pg_data:/var/lib/postgresql/data]
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U aq -d aq2"]
    interval: 5s
    timeout: 3s
    retries: 6
  networks: [aq2]
  # NO ports: mapping (internal only)

api:
  depends_on:
    db: { condition: service_healthy }
  environment:
    DATABASE_URL: postgresql+asyncpg://aq:${POSTGRES_PASSWORD}@db:5432/aq2
    DATABASE_URL_SYNC: postgresql+psycopg://aq:${POSTGRES_PASSWORD}@db:5432/aq2

web:
  environment:
    AQ_API_URL: http://api:8000
    AQ_SESSION_SECRET: ${AQ_SESSION_SECRET}     # required, no default; ≥32 chars
    AQ_COOKIE_SECURE: ${AQ_COOKIE_SECURE:-false}
```

### Env additions (`.env.example`)
```
POSTGRES_PASSWORD=             # required, no default
DATABASE_URL=postgresql+asyncpg://aq:<POSTGRES_PASSWORD>@db:5432/aq2
DATABASE_URL_SYNC=postgresql+psycopg://aq:<POSTGRES_PASSWORD>@db:5432/aq2
AQ_SESSION_SECRET=             # required, ≥32 chars
AQ_COOKIE_SECURE=false         # set true behind TLS proxy
```

---

## Database schema

Two Alembic migrations.

### `0001_initial.py`
Empty baseline. Establishes the migration chain.

### `0002_actors_apikeys_audit.py`

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE actors (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('human','agent','script','routine')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deactivated_at  TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX actors_name_active_uniq ON actors (name) WHERE deactivated_at IS NULL;

CREATE TABLE api_keys (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id              UUID NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
  name                  TEXT NOT NULL,
  key_hash              TEXT NOT NULL,    -- argon2id encoded
  prefix                TEXT NOT NULL,    -- first 8 chars, display only
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at            TIMESTAMPTZ NULL,
  revoked_by_actor_id   UUID NULL REFERENCES actors(id) ON DELETE RESTRICT,
  CHECK ((revoked_at IS NULL) = (revoked_by_actor_id IS NULL))
);
CREATE INDEX api_keys_actor_active_idx ON api_keys (actor_id) WHERE revoked_at IS NULL;
CREATE INDEX api_keys_prefix_idx       ON api_keys (prefix);

CREATE TABLE audit_log (
  id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ts                         TIMESTAMPTZ NOT NULL DEFAULT now(),
  op                         TEXT NOT NULL,
  authenticated_actor_id     UUID NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
  claimed_actor_identity     TEXT NULL,    -- per-call agent_identity (MCP informational)
  target_kind                TEXT NULL,
  target_id                  UUID NULL,
  request_payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
  response_payload           JSONB NULL,
  error_code                 TEXT NULL
);
CREATE INDEX audit_log_ts_idx       ON audit_log (ts DESC);
CREATE INDEX audit_log_actor_ts_idx ON audit_log (authenticated_actor_id, ts DESC);
CREATE INDEX audit_log_op_ts_idx    ON audit_log (op, ts DESC);
CREATE INDEX audit_log_target_idx   ON audit_log (target_kind, target_id) WHERE target_id IS NOT NULL;

-- Helper for first-run advisory lock
-- Used by setup tx: `SELECT pg_advisory_xact_lock(hashtext('aq:setup-singleton'));`
-- No DDL needed; advisory locks are session/tx-scoped, not stored.
```

---

## Stories (12)

Each story body in Plane uses ADR-AQ-030 bounded-fields shape. Plan-level summaries below; full ticket bodies authored at ticket-create time. Per Ghost: every story carries a "Why this matters (human outcome)" line.

---

### Story 2.1 — Postgres + SQLAlchemy + Alembic scaffold

**Why this matters (human outcome).** AQ 2.0 gets a real place to remember things. State survives a restart for the first time. Every later capability has somewhere to put data.

**Objective.** Add `db` to compose. Install async-SA + Alembic + asyncpg + psycopg + pydantic-settings + argon2-cffi. Scaffold `apps/api/alembic/` with `env.py`. Ship migration `0001_initial.py` (empty baseline). Wire `aq_api/_db.py` (async engine + session factory + FastAPI dep). Update `apps/api/Dockerfile` entrypoint to run `alembic upgrade head` before uvicorn.

**Scope (in).** `docker-compose.yml` (new `db` service, named volume, internal-only); `apps/api/pyproject.toml` deps; `apps/api/src/aq_api/_db.py`; `apps/api/src/aq_api/_settings.py`; `apps/api/alembic.ini`; `apps/api/alembic/env.py`; `apps/api/alembic/versions/0001_initial.py`; `apps/api/Dockerfile` entrypoint update; root `.env.example`.

**Scope (out).** No tables, no models, no app endpoints touch DB.

**Security guardrails.** `DATABASE_URL` and `POSTGRES_PASSWORD` env-only — no fallback strings in code. `db` service has no `ports:` mapping. Alembic uses `DATABASE_URL_SYNC` separately so async URL never leaks into sync land. `_settings.py` validates `POSTGRES_PASSWORD` is set at module load — fails fast.

**KISS/DRY.** Reuses cap #1 compose conventions. `_settings.py` follows the env-only pattern from `_version.py`. All handlers `async def`.

**Verification.**
1. `docker compose up -d db && docker compose exec db pg_isready -U aq -d aq2` → exit 0.
2. `docker compose up -d api` → entrypoint logs show `alembic upgrade head` then uvicorn boot.
3. `docker compose exec api uv run alembic current` → `0001_initial (head)`.
4. Re-running `alembic upgrade head` is a no-op (idempotence).

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S1-01 | Postgres up healthy under compose | command | `db-up.txt` | pg_isready exit 0 |
| DOD-AQ2-S1-02 | API entrypoint runs migrations before uvicorn | command | `api-boot.txt` (entrypoint logs) | shows `alembic upgrade head` then `Uvicorn running` |
| DOD-AQ2-S1-03 | Idempotent upgrade (run twice) | command | `alembic-idempotent.txt` | second run exit 0, no schema change |
| DOD-AQ2-S1-04 | Boot fails loud if `POSTGRES_PASSWORD` unset | command | `boot-fail-no-password.txt` | api container exits non-zero with clear error |

**Depends on.** None within cap #2. Hard preconditions (AQ2-15, AQ2-18 amendment) on `main`.

---

### Story 2.2 — Schema migration: `actors`, `api_keys`, `audit_log`

**Why this matters (human outcome).** The data model that makes "who did this?" answerable is real. Three tables, every constraint that prevents an invalid identity state at the DB level, not just app level.

**Objective.** Land migration `0002_actors_apikeys_audit.py`. Three tables, all indexes, the partial unique index on `actors.name`, the symmetric NULL CHECK on `api_keys`. Add SQLAlchemy 2.0 ORM models in `apps/api/src/aq_api/models/db.py` with `Mapped[...]` typed style.

**Scope (in).** `apps/api/alembic/versions/0002_actors_apikeys_audit.py`; `apps/api/src/aq_api/models/db.py`; `apps/api/tests/test_db_schema.py`.

**Scope (out).** No Pydantic contract models (Story 2.3). No CRUD.

**Security guardrails.** All FKs `ON DELETE RESTRICT` — nothing cascades. Symmetric NULL CHECK rejects half-revoked rows at DB level (defense in depth). Partial unique index on `actors.name` allows reuse only after deactivation.

**KISS/DRY.** Every datetime is `TIMESTAMPTZ` — no naive datetimes. ORM uses SA 2.0 typed mappings; no legacy `Column()` style.

**Verification.**
1. `alembic upgrade head` → 3 tables visible via `\dt`.
2. `alembic downgrade base && alembic upgrade head` → clean.
3. Insert with `revoked_at` set but `revoked_by_actor_id` NULL → CHECK fails.
4. Insert two active actors with same name → unique violation.
5. Deactivate one, then create new active actor with same name → succeeds.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S2-01 | All three tables + indexes exist after migration | command | `schema-structure.txt` (parsed `\d+` output, redacted) | table+column+constraint sets match committed snapshot |
| DOD-AQ2-S2-02 | Partial unique index permits name reuse only after deactivation | test | `schema-test-report.xml` | test passes |
| DOD-AQ2-S2-03 | Symmetric NULL CHECK rejects half-revoked rows | test | `schema-test-report.xml` | test passes |
| DOD-AQ2-S2-04 | Downgrade-then-upgrade is clean | command | `alembic-down-up.txt` | both exit 0; final `\dt` matches start |

**Depends on.** AQ2-S1.

---

### Story 2.3 — Pydantic contract models

**Why this matters (human outcome).** Every wire payload that crosses an AQ surface from this point forward has a single, frozen, type-checked shape. No surface can secretly add a field. No surface can leak a key by accident — the response model literally cannot include a `key_hash`.

**Objective.** Pydantic v2 wire types for cap #2: `Actor`, `ApiKey` (display), `AuditLogEntry`, `SetupRequest`, `SetupResponse`, `CreateActorRequest`, `CreateActorResponse`, `RevokeApiKeyResponse`, `AuditQueryParams`, `WhoamiResponse`, `ListActorsResponse`, `AuditLogPage`. All `extra="forbid"`, `frozen=True`. UTC validators via `_datetime.parse_utc`.

**Scope (in).** `apps/api/src/aq_api/models/auth.py`, `audit.py`, `__init__.py` exports; tests.

**Scope (out).** No DB writes, no endpoints.

**Security guardrails.** `ApiKey` (display) NEVER carries `key_hash` or plaintext `key` — only `id`, `actor_id`, `name`, `prefix`, timestamps. `CreateActorResponse.key` and `SetupResponse.founder_key` are `Field(repr=False)` so `repr()` cannot leak. Add unit tests asserting `repr()` does not contain the plaintext.

**KISS/DRY.** Same `ConfigDict` shape as `HealthStatus` from cap #1. Same `field_validator` UTC pattern.

**Verification.**
1. `mypy --strict apps/api/src/aq_api/models/` → clean.
2. `pytest test_auth_models.py test_audit_models.py` → all pass.
3. `Actor(kind="god")` → ValidationError.
4. `repr(CreateActorResponse(...))` does NOT contain plaintext key chars.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S3-01 | Wire models compile under mypy --strict | command | `typecheck.txt` | mypy exit 0 |
| DOD-AQ2-S3-02 | Display models forbid key_hash + plaintext key | test | `auth-models-test.xml` | test passes |
| DOD-AQ2-S3-03 | `__repr__` redacts plaintext keys | test | `auth-models-test.xml` | test passes |
| DOD-AQ2-S3-04 | All datetime fields reject naive datetimes | test | `audit-models-test.xml` | test passes |

**Depends on.** AQ2-S2.

---

### Story 2.4 — Service layer + Bearer auth (REST + MCP HTTP + stdio) — corrected model

**Why this matters (human outcome).** AQ 2.0 stops accepting unauthenticated requests. From now on, every byte of state has a fingerprint — a named Actor, attached at the door. The same enforcement holds across REST, MCP HTTP, MCP stdio, and (later) Web.

**Objective.** Two pieces:

1. **Service layer.** Create `apps/api/src/aq_api/services/` with pure-Python service functions: `services.auth.resolve_actor(session, key) -> Actor | None`. REST handlers AND MCP tool handlers (later stories) call these. No HTTP delegation between surfaces.

2. **Bearer auth dependency.** `apps/api/src/aq_api/_auth.py` exposes FastAPI dependency `current_actor()` that: parses `Authorization: Bearer <key>`, extracts 8-char prefix, queries candidate `api_keys` rows with that prefix where `revoked_at IS NULL`, verifies plaintext via argon2 (constant-time), returns `Actor`, OR raises `HTTPException(401)` with byte-equal opaque body `{"error":"unauthenticated"}`. Populates request contextvar `authenticated_actor_id`. Applied to BOTH `/<rest paths>` AND `/mcp` mount.

**Scope (in).** `apps/api/src/aq_api/services/auth.py`; `apps/api/src/aq_api/_auth.py`; `apps/api/src/aq_api/_request_context.py` (ContextVars `authenticated_actor_id` + `claimed_actor_identity`); `apps/api/tests/test_auth.py`. Update `apps/api/src/aq_api/app.py` to apply auth dep to MCP mount.

**Scope (out).** Audit log writes (Story 2.5). MCP `agent_identity` plumbing (Story 2.10).

**Security guardrails.**
- 401 body is byte-equal across every failure mode (no enumeration).
- Plaintext key NEVER logged; FastAPI access-log scrubs `Authorization` header (configure custom logger).
- Revocation is live (no result cache) — every request hits DB.
- Multi-prefix-collision: try every candidate; reject only after all verify failures.
- argon2 verifier is module-level singleton.
- All handlers `async def`.

**KISS/DRY.** Service functions are the source of truth. REST + MCP both call them. ContextVar pattern reused later.

**Verification.**
1. Missing header → 401 byte-equal body.
2. Bogus token → 401.
3. Revoked key → 401.
4. Valid key for deactivated actor → 401.
5. MCP HTTP `/mcp` without Bearer → 401 (same as REST).
6. Server logs grep for plaintext key fragment → 0 hits.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S4-01 | All five auth-rejection cases return byte-equal 401 | test | `auth-test.xml` | tests pass |
| DOD-AQ2-S4-02 | Revocation is live (no cache regression) | test | `auth-test.xml` | revocation-during-session test passes |
| DOD-AQ2-S4-03 | Plaintext keys never appear in logs | command | `auth-log-scan.txt` (redacted before commit) | grep returns 0 hits |
| DOD-AQ2-S4-04 | MCP HTTP requires Bearer same as REST | test | `mcp-auth-test.xml` | unauth MCP request returns 401 |

**Depends on.** AQ2-S2, AQ2-S3.

---

### Story 2.5 — Audit log writer (same-transaction, mutation-only)

**Why this matters (human outcome).** When a mutation succeeds, the world changed AND we know who did it — both committed together, no orphan rows on either side. When something fails halfway, both the change AND the audit row roll back together. No "I see audit but state didn't change" or vice versa. This is the trust foundation cap #2 promises.

**Objective.** `apps/api/src/aq_api/_audit.py`: `async def record(session, *, op, target_kind, target_id, request_payload, response_payload, error_code) -> None` — inserts an `audit_log` row using **the same `AsyncSession` passed in by the caller**. Plus `audited_op(...)` async context manager that wraps a domain mutation: success commits both rows atomically; exception rolls both back. Reads `authenticated_actor_id` + `claimed_actor_identity` from contextvars. Includes recursive secret redactor.

**Scope (in).** `apps/api/src/aq_api/_audit.py`; `apps/api/src/aq_api/services/audit.py` (recursive redactor + write helper); tests including the **rollback symmetry adversarial test**.

**Scope (out).** Per-op integration (subsequent stories). Audit query (Story 2.9).

**Audit semantics (locked).**
- Reads NEVER audit. The `record()` helper is only called from mutation handlers.
- Mutation success → commit domain row + audit row together.
- Mutation business-rule denial (403, 409) → commit audit-only row with `error_code` set; no domain row. Domain handler raises `BusinessRuleException`; `audited_op` context catches it, writes audit row, then re-raises as the appropriate HTTP error.
- Unexpected exception (5xx) → roll back; no audit row. Logged separately.
- Validation error (422 from Pydantic) → no audit row; mutation never started.

**Security guardrails.** Recursive redactor on `request_payload` and `response_payload` BEFORE JSONB serialization. Pattern: `(?i)(^|_)(key|token|secret|password|hash)(_|$)`. Tested with planted secrets at multiple nesting levels.

**KISS/DRY.** One helper, one shape. Every mutation funnels through it. No surface writes audit rows directly.

**Verification.**
1. Domain insert + audit insert succeed → both committed.
2. Domain insert succeeds, audit insert raises (mocked) → both rolled back.
3. Domain insert raises BusinessRuleException → audit row committed with `error_code`, domain not committed, exception re-raised.
4. Domain insert raises unexpected exception → both rolled back, no audit row.
5. Recursive redactor strips secrets at multiple JSON nesting levels.
6. Two concurrent calls with different `claimed_actor_identity` → audit rows reflect own values.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S5-01 | Domain + audit roll back atomically on audit-row failure | test | `atomicity-test.xml` | rollback-symmetry passes |
| DOD-AQ2-S5-02 | Business-rule denial commits audit-only row | test | `audit-denial-test.xml` | audit row exists, domain row absent |
| DOD-AQ2-S5-03 | Recursive redactor strips secrets at any depth | test | `audit-redactor-test.xml` | planted secrets at 3 nesting levels all redacted |
| DOD-AQ2-S5-04 | ContextVar task-scoped under concurrent calls | test | `audit-contextvar-test.xml` | concurrent test passes |

**Depends on.** AQ2-S4.

---

### **Hard checkpoint C1** — fires after Story 2.5 lands

Auth + audit primitives exist with passing tests. Codex stops, posts evidence on epic, awaits Ghost approval before Story 2.6.

---

### Story 2.6 — `setup` op (REST + CLI; first-run only; auditless)

**Why this matters (human outcome).** Mario's first command on a fresh AQ 2.0 install. Types `aq setup`, gets a key, knows it's his alone. The single bootstrap moment that locks AQ 2.0 to its operator. After this, no one else can claim founder identity.

**Objective.** `POST /setup` and `aq setup`. **Auditless** (per locked rule). First-run-only enforced via Postgres advisory lock + `EXISTS(SELECT 1 FROM actors)` check. Creates founder Actor (`name="founder"`, `kind="human"`), mints founder API key, returns `SetupResponse{founder_key, actor_id}`. CLI writes `~/.aq/config.toml` mode 0600.

(NOTE: No "claude-mcp-bridge" actor — that concept is dropped per the corrected MCP auth model. MCP HTTP just requires the founder's key.)

**Scope (in).** `apps/api/src/aq_api/services/setup.py` + `routes/setup.py`; `apps/cli/src/aq_cli/main.py` (new `setup` command); `apps/cli/src/aq_cli/_config.py` (TOML reader/writer); tests.

**Scope (out).** No MCP `setup` (host-local only). No Web `setup` UI.

**Security guardrails.**
- `~/.aq/config.toml` mode 0600 on POSIX; ACL-restricted to current user on Windows.
- CLI refuses to overwrite existing config without `--force`.
- First-run gate: advisory lock `pg_advisory_xact_lock(hashtext('aq:setup-singleton'))` before the existence check. Concurrent calls serialize.
- Setup endpoint rate-limit: 5 attempts/min while open.
- Founder key returned ONCE in response; never re-fetchable.
- Validation script artifact for setup is redacted before commit (script pipes through `redact-evidence.sh`).

**KISS/DRY.** First-run gate is one DB call. Config TOML follows pattern of standard `~/.aws/config` style.

**Verification.**
1. Fresh DB: `curl -X POST localhost:8001/setup` → 200 with `founder_key`. (Artifact redacted before commit.)
2. Re-run: 409 byte-equal `{"error":"already_initialized"}`.
3. Concurrent race: 10 parallel calls → exactly one 200, nine 409.
4. `~/.aq/config.toml` mode 0600 (POSIX).
5. **No** `audit_log` row for `setup` (per locked rule).

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S6-01 | Setup creates founder + key on fresh DB | test | `setup-test.xml` | 1 actor + 1 key after success |
| DOD-AQ2-S6-02 | Re-run returns 409 byte-equal | command | `setup-rerun-409.txt` | 409 + body matches |
| DOD-AQ2-S6-03 | Concurrent setup race → exactly one 200 | test | `setup-race-test.xml` | exactly one 200 in 10 concurrent calls |
| DOD-AQ2-S6-04 | `~/.aq/config.toml` mode 0600 on POSIX | command | `config-toml-perms.txt` | `stat` shows 600 |
| DOD-AQ2-S6-05 | NO audit row for setup | test | `setup-audit-empty.txt` | `audit_log` count is 0 after setup |
| DOD-AQ2-S6-06 | Founder key redacted in committed artifacts | command | `evidence-redaction.txt` | grep for full token returns 0 hits in `cap-02/` |

**Depends on.** AQ2-S4, AQ2-S5.

---

### Story 2.7 — `whoami` + `list_actors` + `create_actor` (REST + CLI + MCP HTTP)

**Why this matters (human outcome).** Mario can ask AQ "who am I?" from any surface and get the same answer. He can see who else has access. He can mint a new identity for an agent (e.g., Claude Code) and hand it a key. The four-surface promise extends to identity.

**Objective.** `GET /actors/me` (whoami), `GET /actors` (paginated, opaque cursor), `POST /actors` (create + mint plaintext key in `CreateActorResponse`). CLI: `aq whoami`, `aq actor list`, `aq actor create`. MCP HTTP tools: `get_self`, `list_actors`, `create_actor` — all calling shared service functions, all requiring caller's Bearer.

**Scope (in).** `apps/api/src/aq_api/services/actors.py`; `apps/api/src/aq_api/routes/actors.py`; `apps/api/src/aq_api/mcp.py` (extend with three tools, all `async def`, optional `agent_identity` field); `apps/cli/src/aq_cli/main.py` (subcommands); tests.

**Scope (out).** Per-key mint endpoint (cap #11). Web routes (Story 2.11). Audit on reads (locked: NOT audited).

**Security guardrails.**
- `whoami`, `list_actors` not audited (reads).
- `create_actor` audited (mutation). Audit row writes redacted request_payload + response_payload (the plaintext key is redacted).
- `create_actor` mints key ONCE; subsequent `GET /api-keys/{id}` (later cap) never returns it.
- `list_actors` excludes `deactivated_at IS NOT NULL` rows by default; `?include_deactivated=true` opt-in IS audited (it's a sensitive read, not a mutation, but treated as one for trail).
- `extra="forbid"` rejects unknown request fields with 422.

**KISS/DRY.** All handlers `async def`. REST handlers signature: `Annotated[Actor, Depends(current_actor)]`. CLI extends `_post`/`_delete`. MCP tools delegate to service functions (NOT httpx).

**Verification.**
1. `whoami` byte-equal across REST/CLI/MCP.
2. `create_actor` then `list_actors` shows new row.
3. Audit log: 0 rows for whoami calls; 1 row for create_actor; 0 rows for list_actors (default scope).
4. Missing Bearer → 401 byte-equal across surfaces.
5. `POST /actors {"extra_field": true}` → 422.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S7-01 | whoami byte-equal across REST/CLI/MCP | test | `whoami-parity.txt` (redacted) | identical Actor JSON across surfaces |
| DOD-AQ2-S7-02 | create_actor mints one-shot plaintext key + audit row redacted | test | `create-actor.xml` + `audit-after-create.json` (redacted) | response carries key once; audit row has redacted payloads |
| DOD-AQ2-S7-03 | Reads write zero audit rows | test | `read-audit-empty.txt` | audit count unchanged across read calls |
| DOD-AQ2-S7-04 | Pagination cursor round-trips | test | `list-actors-pagination.xml` | next page contains expected next IDs |

**Depends on.** AQ2-S6.

---

### Story 2.8 — `revoke_api_key` (own-key-only; cross-actor 403; last-key 409)

**Why this matters (human outcome).** A leaked key is killable. Mario can revoke any of his own keys from any surface — CLI, MCP, REST. He cannot accidentally revoke someone else's key (403). He cannot lock himself out by revoking his last active key (409). The audit log records every revoke attempt, including the failed cross-actor ones, so leaks-being-tested-in-the-wild leave fingerprints.

**Objective.** `DELETE /api-keys/{id}`. Own-key only. 403 on cross-actor (audit-row-only). 409 on last-active-key. Idempotent on already-revoked own key. CLI: `aq key revoke <id>`. MCP: `revoke_api_key`. Concurrency: `SELECT ... FOR UPDATE` on actor's active keys during revoke.

**Scope (in).** `apps/api/src/aq_api/services/api_keys.py` (revoke logic with row lock); `routes/api_keys.py`; CLI + MCP extensions; tests including adversarial cross-actor revoke + concurrent-revoke race.

**Scope (out).** Web revoke UI (cap #11). `create_api_key` op (cap #11).

**Security guardrails.**
- 403 (not 404) on cross-actor — locked decision per honest single-instance trust.
- Audit row written on 403 with `error_code="forbidden"`, `target_id=<key id>`, `request_payload` redacted.
- `SELECT ... FOR UPDATE` on `api_keys WHERE actor_id=$1 AND revoked_at IS NULL` — concurrent revokes serialize.
- Refuse if revoking would leave actor with zero active keys → 409 + audit `error_code="cannot_revoke_last_key"`.
- Idempotent: revoking an already-revoked own key returns 200 with existing `revoked_at`, audit-logged once.

**KISS/DRY.** Service function used by both REST and MCP. Reuses `_audit.audited_op`.

**Verification.**
1. Own key revoke → 200, key revoked, audit row exists.
2. Cross-actor key revoke → 403, no DB change, audit row with `error_code="forbidden"`.
3. Already-revoked own key → 200, idempotent, no double audit row.
4. Last-active-key revoke → 409, key still active, audit row with `error_code="cannot_revoke_last_key"`.
5. Two concurrent revokes of two different keys for an actor with 2 active keys → first succeeds, second 409 (last-key block).

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S8-01 | Own-key revoke succeeds + audit | test | `revoke-self.xml` | 200, key revoked, audit row |
| DOD-AQ2-S8-02 | Cross-actor revoke 403 + audit `error_code="forbidden"` | test | `revoke-cross-403.txt` (redacted) | 403, no DB change, audit row with error_code |
| DOD-AQ2-S8-03 | Idempotent on already-revoked own key | test | `revoke-idempotent.xml` | second revoke 200, no duplicate audit |
| DOD-AQ2-S8-04 | Last-active-key 409 | test | `revoke-last-key.xml` | 409, key still active |
| DOD-AQ2-S8-05 | Concurrent revoke race serializes correctly | test | `revoke-race-test.xml` | exactly one revoke succeeds; second 409 |

**Depends on.** AQ2-S7.

---

### Story 2.9 — `query_audit_log` (REST + CLI + MCP HTTP)

**Why this matters (human outcome).** Mario can answer "who did what, when, on which target?" from any surface. The audit log isn't an internal black box — it's queryable, paginated, filter-able by Actor, op, time window. Failed mutation attempts (403s, 409s) appear alongside successes, so anomalies surface.

**Objective.** `GET /audit?actor=&op=&since=&until=&limit=50&cursor=`. CLI: `aq audit`. MCP: `query_audit_log`. Time params parse via `_datetime.parse_utc`. Pagination cursor: opaque base64 of `(ts, id)`. `limit` server-clamped at 200.

**Scope (in).** `apps/api/src/aq_api/services/audit.py` (query function + cursor encode/decode); `routes/audit.py`; CLI + MCP; tests including SQL-injection probes.

**Scope (out).** Audit log mutation (it's append-only via `_audit.record`). NOT audited (read).

**Security guardrails.**
- All filter values are bound parameters (SQLAlchemy parameterization).
- Cursor is opaque base64; not signed (no security need beyond opacity in trusted single-instance).
- `limit` server-clamped at 200 regardless of request.
- Any authenticated Actor can read all audit rows (single-instance trust).
- Response payloads already redacted at write-time (Story 2.5); query returns them as-stored.

**KISS/DRY.** Same pagination shape as `list_actors`. Same datetime parser.

**Verification.**
1. After Stories 2.6–2.8: `aq audit` returns ≥3 rows (create_actor, revoke success, revoke-cross-actor-403).
2. `?op=create_actor` filters to expected count.
3. REST/CLI/MCP same query → byte-equal.
4. SQL-injection probe `?actor=foo' OR '1'='1` → zero rows (no error, no overflow).
5. `?limit=10000` → response contains ≤200 rows.
6. `?since=<future>` → empty list, not error.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S9-01 | Audit query parity across surfaces | test | `audit-parity.txt` | identical results |
| DOD-AQ2-S9-02 | SQL-injection probes return zero rows | test | `audit-injection.txt` | 4 probes all return 0 rows |
| DOD-AQ2-S9-03 | Pagination cursor round-trips | test | `audit-pagination.xml` | no duplicates across pages |
| DOD-AQ2-S9-04 | `limit` server-clamped at 200 | test | `audit-limit-clamp.xml` | request 10000 returns ≤200 |

**Depends on.** AQ2-S8.

---

### Story 2.10 — MCP `agent_identity` informational field

**Why this matters (human outcome).** When Claude Code calls AQ on behalf of a specific model (e.g. `claude-opus-4-7`), the audit log records both: the API key bearer (Mario's host) AND the calling-on-behalf-of identity. The audit story stays accurate when one human's key is being used by multiple agents in turn.

**Objective.** Add optional `agent_identity: str | None` field to every MCP tool's input schema (cap #1 + cap #2 tools). When provided (non-empty), populate request contextvar `claimed_actor_identity` before service-layer call. Audit row records both `authenticated_actor_id` (always the Bearer's actor) and `claimed_actor_identity` (the field, or NULL).

**Scope (in).** `apps/api/src/aq_api/mcp.py` — extend each tool. `apps/api/src/aq_api/_request_context.py` — extend if needed. `tests/parity/mcp_schema.snapshot.json` — regenerated.

**Scope (out).** No new ops. No "bridge" actor (dropped from corrected model).

**Security guardrails.**
- `agent_identity` is INFORMATIONAL only. Authentication remains the Bearer.
- Empty string treated as NULL.
- ContextVar reset per-call (each MCP tool call is its own asyncio task).
- All tool handlers `async def`.
- Non-empty `agent_identity` validated as `min_length=1, max_length=200, pattern="^[A-Za-z0-9_./:-]+$"` to prevent injection-style payloads in audit rows.

**KISS/DRY.** Single contextvar already exists from Story 2.4. MCP shim is the only writer of `claimed_actor_identity`. No bridge actor — simpler than the original AQ2-18 model.

**Verification.**
1. MCP `create_actor` with `agent_identity="claude-opus-4-7"` → audit row has Bearer's `authenticated_actor_id` + `claimed_actor_identity="claude-opus-4-7"`.
2. MCP `create_actor` with NO `agent_identity` → audit row has Bearer's `authenticated_actor_id` + `claimed_actor_identity=NULL`.
3. REST `POST /actors` (no MCP) → audit row has `claimed_actor_identity=NULL`.
4. Two concurrent MCP calls with different `agent_identity` → audit rows reflect own values (no crossover).
5. `agent_identity="<script>"` → 422 (regex rejection).

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S10-01 | Audit rows record both authenticated_actor_id and claimed_actor_identity correctly | test | `mcp-agent-identity-audit.xml` | values match expected per call shape |
| DOD-AQ2-S10-02 | Empty/missing agent_identity → claimed_actor_identity NULL | test | `mcp-empty-identity.xml` | NULL recorded |
| DOD-AQ2-S10-03 | ContextVar leak test (concurrent calls) | test | `mcp-contextvar-concurrent.xml` | each call's audit row matches own identity |
| DOD-AQ2-S10-04 | Pattern rejects injection-shaped values | test | `mcp-identity-pattern.xml` | `<script>`, `';--`, etc → 422 |
| DOD-AQ2-S10-05 | MCP schema snapshot regenerated and committed | artifact | `tests/parity/mcp_schema.snapshot.json` | snapshot byte-equal to live `tools/list` |

**Depends on.** AQ2-S7.

---

### Story 2.11 — Web `/login` + `/logout` + `/whoami` panel

**Why this matters (human outcome).** A human can open AQ 2.0 in a browser, paste their key once, and stay signed in for the session. The page proves "yes, you're authenticated as <name>." No CLI required. Cap #11 will build the four read-only views on top of this auth scaffolding.

**Objective.** Server-rendered `/login` (paste API key → iron-session encrypts into httpOnly+secure(env-driven)+sameSite=strict cookie). `/logout` clears cookie. `/whoami` panel shows the authenticated Actor pulled via server-side fetch to FastAPI with cookie-stored Bearer. **Browser never sees plaintext.** No `/actors` view, no `/audit` view (those are CLI/MCP/REST per cap #11 scope).

**Scope (in).** `apps/web/app/login/page.tsx` + `app/login/route.ts`; `apps/web/app/lib/session.ts` (iron-session config); `apps/web/app/api/actors/me/route.ts` (proxy); `apps/web/app/whoami/page.tsx`; `apps/web/app/logout/route.ts`; `apps/web/package.json` deps; compose env mapping; e2e Playwright.

**Scope (out).** No `/actors` Web view (cap #11). No `/audit` Web view (cap #11 scope guardrail forbids audit-log browser). No mint/revoke UI (cap #11). No `setup` UI.

**Security guardrails.**
- Cookie flags: `httpOnly: true`, `secure: process.env.AQ_COOKIE_SECURE === "true"`, `sameSite: "strict"`, `maxAge: 8h`, `path: "/"`.
- `AQ_SESSION_SECRET` validated at boot (≥32 chars); web service exits if missing.
- CSRF: covered by sameSite=strict + same-origin route handlers + httpOnly. No separate CSRF token.
- Login form `POST /login` → server route only. Never to FastAPI directly.
- No client-side JS reads `document.cookie` (test asserts).
- Server logs scrub plaintext key (test grep returns 0 hits).
- Invalid-key login returns same response as no-key (no enumeration).

**KISS/DRY.** Reuses cap #1 `route.ts` proxy pattern (`upstreamUrl(path)`, `cache:"no-store"`, 502 on upstream error).

**Verification.**
1. Submit valid key → 302 to `/whoami`.
2. DevTools: cookie httpOnly; `document.cookie` returns empty for session cookie name.
3. `curl /api/actors/me` without cookie → 401.
4. Logout clears cookie.
5. Server logs grep for known sentinel key chars → 0 hits.
6. Playwright: `/login` → submit valid → `/whoami` panel renders Actor name + id.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S11-01 | Cookie httpOnly; not visible to `document.cookie` | test | `web-cookie-flags.png` (DevTools) + `web-cookie-test.xml` | httpOnly=true, JS read returns empty |
| DOD-AQ2-S11-02 | Server logs scrubbed of plaintext key | command | `web-log-scan.txt` (redacted) | grep returns 0 hits |
| DOD-AQ2-S11-03 | Invalid-key login same response as no-key | test | `web-login-enumeration.xml` | byte-equal 401 page |
| DOD-AQ2-S11-04 | Playwright e2e: login → /whoami renders Actor | test | `web-e2e.xml` + `whoami-screenshot.png` | passes |

**Depends on.** AQ2-S7.

---

### Story 2.12 — Parity tests + CI workflow updates + atomicity

**Why this matters (human outcome).** Cap #2's auth + audit promise becomes mechanically enforced. From now on, any future PR that breaks 4-surface byte equality, transactional rollback symmetry, or audit-mutation-only rule fails CI. The contract is the moat; the moat is alive in code, not just in the plan.

**Objective.** Extend `tests/parity/test_four_surface_parity.py` with cap #2 cases. Add `tests/parity/test_audit_atomicity.py`. Update CI workflows for Postgres service. Author `scripts/validate-cap02.sh` + `.ps1`.

**Scope (in).** `tests/parity/test_four_surface_parity.py` (extend); `tests/parity/test_audit_atomicity.py` (new); `tests/parity/conftest.py` (add `db_url`, `truncate_db`, `redact_evidence` fixtures); `.github/workflows/test.yml` + `parity.yml` (add Postgres + new env); `scripts/validate-cap02.sh` + `.ps1`; `scripts/redact-evidence.sh`.

**Scope (out).** No new ops.

**Security guardrails.**
- Test DB is `aq2_test` (separate from dev DB).
- Secrets pulled from GH Actions secrets where possible; `AQ_FOUNDER_KEY` exported at runtime from setup response, never committed.
- Test DB truncated between tests via `truncate_db` fixture.
- All artifacts redacted by `redact-evidence.sh` before commit.

**KISS/DRY.** Reuses `api_base_url`, `mcp_base_url`, `update_snapshots` fixtures. Reuses `_assert_commit_matches_head()`.

**Verification.**
1. `uv run pytest tests/parity/` → all cap #1 + cap #2 tests pass.
2. `bash scripts/validate-cap02.sh` produces every redacted artifact.
3. `gh run view` for `parity.yml` → green.
4. Atomicity test fails the build if rollback regresses.
5. `redact-evidence.sh` strips known patterns from sample inputs.

**DoD items.**

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-AQ2-S12-01 | All cap #1 + cap #2 parity tests pass | test | `parity-test-report.xml` | all pass |
| DOD-AQ2-S12-02 | Atomicity test asserts rollback symmetry + audit-only-on-denial | test | `atomicity-test-report.xml` | both cases pass |
| DOD-AQ2-S12-03 | CI workflows green (lint, test, build, parity) | artifact | `ci-run.txt` (redacted) | all 4 conclusion=success |
| DOD-AQ2-S12-04 | `_assert_commit_matches_head()` invariant carries forward | test | `commit-matches-head.txt` | exit 0 |
| DOD-AQ2-S12-05 | redact-evidence.sh strips known secret patterns | test | `redactor-test.xml` | sample inputs sanitized |

**Depends on.** AQ2-S11.

---

### **Hard checkpoint C2** — fires after Story 2.12 lands

Full Docker stack (api + db + web) up healthy. Atomicity + parity tests green. All artifacts redacted. Codex stops, posts evidence on epic, awaits Ghost approval before opening the PR.

### **Hard checkpoint C3** — fires when the PR is opened

Codex opens ONE PR. Awaits Ghost merge approval. Does NOT self-merge.

---

## Capability-level DoD list (15 entries)

| dod_id | statement | verification_method | evidence_required | acceptance_threshold |
|---|---|---|---|---|
| DOD-CAP02-01 | Postgres + SA + Alembic scaffold; api boots with migrations | command | `alembic-current.txt`, `api-boot.txt` | `0002_*` head; entrypoint runs migrations |
| DOD-CAP02-02 | Three tables + indexes + constraints exist | command | `schema-structure.txt` (parsed, redacted) | matches snapshot |
| DOD-CAP02-03 | Pydantic models compile under mypy --strict; reprs redact secrets | command | `typecheck.txt`, `auth-models-test.xml` | mypy clean; tests pass |
| DOD-CAP02-04 | Bearer auth on REST + MCP HTTP rejects bad/missing/revoked with byte-equal 401 | test | `auth-test.xml`, `mcp-auth-test.xml` | tests pass |
| DOD-CAP02-05 | Audit writer rolls back domain + audit together; commits audit-only on denial | test | `atomicity-test-report.xml` | both cases pass |
| DOD-CAP02-06 | Setup is auditless, first-run-only, advisory-locked | test | `setup-test.xml`, `setup-race-test.xml`, `setup-audit-empty.txt` | all pass |
| DOD-CAP02-07 | whoami byte-equal across REST/CLI/MCP; reads not audited | test | `whoami-parity.txt`, `read-audit-empty.txt` | identical JSON; audit unchanged on reads |
| DOD-CAP02-08 | create_actor mints one-shot key + audit row redacted | test | `create-actor.xml`, `audit-after-create.json` | response carries key once; audit redacted |
| DOD-CAP02-09 | revoke_api_key own-key-only with 403 + audit; last-key 409; concurrent race serialized | test | `revoke-cross-403.txt`, `revoke-last-key.xml`, `revoke-race-test.xml` | all pass |
| DOD-CAP02-10 | query_audit_log parity + SQL-injection probes return zero rows | test | `audit-parity.txt`, `audit-injection.txt` | identical; injection 0 rows |
| DOD-CAP02-11 | MCP agent_identity populates claimed_actor_identity; pattern rejects injection | test | `mcp-agent-identity-audit.xml`, `mcp-identity-pattern.xml` | values correct; injection 422 |
| DOD-CAP02-12 | Web cookie httpOnly + secure(env-driven); plaintext never reaches browser or logs | test + command | `web-cookie-flags.png`, `web-log-scan.txt` | flags correct; 0 plaintext hits |
| DOD-CAP02-13 | docker compose up -d --build --wait brings api+db+web healthy | command | `docker-up.txt` | all three healthy <60s |
| DOD-CAP02-14 | All CI workflows green (lint, test, build, parity) | artifact | `ci-run.txt` (redacted) | 4× success |
| DOD-CAP02-15 | All committed artifacts pass `gitleaks` and `redact-evidence.sh` | command | `redactor-pass.txt`, `gitleaks-pass.txt` | both report 0 leaks |

---

## Validation script (`scripts/validate-cap02.sh` outline)

```bash
mkdir -p plans/v2-rebuild/artifacts/cap-02
ART=plans/v2-rebuild/artifacts/cap-02
RED=scripts/redact-evidence.sh

# 1. Clean rebuild + schema reset
docker compose down 2>&1 | tee $ART/docker-down.txt    # NOTE: no -v (volumes preserved)
AQ_GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build --wait 2>&1 | tee $ART/docker-up.txt
docker compose exec -T api uv run alembic -c apps/api/alembic.ini downgrade base
docker compose exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
docker compose exec -T api uv run alembic -c apps/api/alembic.ini current | tee $ART/alembic-current.txt

# 2. Schema dump (structural only, redacted)
docker compose exec -T db pg_dump -s -U aq aq2 | $RED > $ART/schema-structure.txt

# 3. First-run setup (REDACT before tee)
SETUP=$(curl -sf -X POST http://localhost:8001/setup -H 'content-type: application/json' -d '{"name":"founder"}')
echo "$SETUP" | $RED > $ART/setup-firstrun.txt
KEY=$(echo "$SETUP" | jq -r .founder_key)

# 4. Re-run setup → must 409
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8001/setup \
  -H 'content-type: application/json' -d '{"name":"hacker"}' | tee $ART/setup-rerun-409.txt
test "$(cat $ART/setup-rerun-409.txt)" = "409"

# 5. whoami parity
curl -sf -H "Authorization: Bearer $KEY" http://localhost:8001/actors/me | $RED > $ART/whoami-rest.json
AQ_API_URL=http://localhost:8001 AQ_API_KEY=$KEY uv run aq whoami | $RED > $ART/whoami-cli.json
AQ_API_URL=http://localhost:8001 AQ_API_KEY=$KEY uv run python -m tests.parity.mcp_harness get_self \
  --agent-identity "validate-cap02" | $RED > $ART/whoami-mcp.json
diff <(jq -S .id $ART/whoami-rest.json) <(jq -S .id $ART/whoami-cli.json)
diff <(jq -S .id $ART/whoami-rest.json) <(jq -S .id $ART/whoami-mcp.json)

# 6. create_actor + adversarial cross-actor revoke
NEW=$(curl -sf -X POST -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"name":"actor-b","kind":"agent"}' http://localhost:8001/actors)
echo "$NEW" | $RED > $ART/create-actor.txt
NEW_KEY_ID=$(echo "$NEW" | jq -r .api_key.id)

# Founder tries to revoke actor-b's key → 403
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE -H "Authorization: Bearer $KEY" \
  http://localhost:8001/api-keys/$NEW_KEY_ID | tee $ART/revoke-cross-403.txt
test "$(cat $ART/revoke-cross-403.txt)" = "403"

# 7. Atomicity test
docker compose exec -T api uv run pytest tests/parity/test_audit_atomicity.py \
  --junit-xml=cap-02-atomicity.xml
docker compose cp api:/app/cap-02-atomicity.xml $ART/atomicity-test-report.xml

# 8. Parity suite
docker compose exec -T api uv run pytest tests/parity/ --junit-xml=cap-02-parity.xml
docker compose cp api:/app/cap-02-parity.xml $ART/parity-test-report.xml

# 9. SQL-injection probe
curl -sf -H "Authorization: Bearer $KEY" \
  "http://localhost:8001/audit?actor=foo%27%20OR%20%271%27%3D%271" | $RED > $ART/audit-injection.txt
test "$(jq '.entries | length' $ART/audit-injection.txt)" = "0"

# 10. Web e2e (Playwright)
PLAYWRIGHT_USE_DOCKER=1 AQ_LOGIN_KEY=$KEY pnpm --filter @agenticqueue/web playwright test \
  e2e/auth.spec.ts --reporter=junit > $ART/web-e2e.xml

# 11. COMMIT_MATCHES_HEAD
python -c "import json, subprocess, urllib.request; sha=subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True).stdout.strip(); v=json.loads(urllib.request.urlopen('http://localhost:8001/version').read()); assert v['commit']==sha; print('COMMIT_MATCHES_HEAD', sha)" \
  | tee $ART/commit-matches-head.txt

# 12. CI run summary
{ for wf in lint test build parity; do
    echo "## $wf"; gh run list --workflow=$wf.yml --limit=1 --json url,status,conclusion
  done; } | tee $ART/ci-run.txt

# 13. Final redaction sweep — gitleaks-style scan on artifacts dir
gitleaks detect --source $ART --no-git --report-path=$ART/gitleaks-pass.txt --exit-code 0 || true
test "$(jq '.[] | length' $ART/gitleaks-pass.txt 2>/dev/null || echo 0)" = "0" || \
  { echo "GITLEAKS FOUND SECRETS — fix before commit"; exit 1; }
echo "REDACTOR_PASS_OK" | tee $ART/redactor-pass.txt
```

PowerShell version mirrors line-by-line.

---

## Done-when

- All 12 child story tickets merged into `aq2-cap-02`.
- Validation script runs end-to-end against the running services.
- All 15 capability DoD items have `status="passed"` with redacted evidence in `plans/v2-rebuild/artifacts/cap-02/`.
- Submission payload references every DoD id and points each at a real artifact.
- Capability #2 marked `[DONE]` in `capabilities.md` with merge SHA + validation date + one-line `## Log` entry.
- Ghost approves capability #3 to start.

---

## Risks / deviations (declared in submission)

- **argon2 over bcrypt.** Reject 72-byte truncation footgun. Suggest ADR-AQ-031 in cap #2 or cap #3.
- **App-side hashing only.** pgcrypto for `gen_random_uuid()` only.
- **iron-session over NextAuth.** Smaller blast radius; cap #11 may revisit.
- **First-run gate via DB advisory lock + EXISTS.** No sentinel file.
- **MCP HTTP requires caller's Bearer.** Drops the original AQ2-18 "claude-mcp-bridge" model — that was a security hole. AQ2-18 is rewritten before cap #2.
- **`agent_identity` is informational only.** Optional MCP field; never affects authentication.
- **Setup is auditless.** Founder + Actor rows themselves are bootstrap evidence.
- **Reads are not audited.** Cap statement says "every mutation"; audit-on-reads would be noise.
- **Cap #2 mints API keys via `create_actor` on REST/CLI/MCP.** Conflicts with capabilities.md "API keys are minted in the UI by a human only" — acknowledged deviation, justified by no-UI-yet. Cap #11 will be the canonical mint UX.
- **403 vs 404 on cross-actor revoke.** 403 chosen for honesty in trusted single-instance.
- **Web ships only `/login` + `/logout` + `/whoami`.** Drops `/actors` and `/audit` views per cap #11 scope guardrails.
- **No bridge actor in MCP.** Drops the "claude-mcp-bridge" identity entirely.
- **All handlers `async def`.** ContextVar safety guarantee.
- **Audit JSONB payload-size cap deferred.** Recommended for cap #3+ when domain payloads grow.
- **Test DB host-port unmapped.** Tests run via `docker compose exec` / `docker compose run --rm`.

---

## Out of scope (with owning cap)

- `create_api_key` standalone op (without create_actor) → **cap #11**.
- Domain entities (Project / Workflow / Pipeline / Job) → **cap #3**.
- Claim binding semantics → **cap #4**.
- Run Ledger queries → **cap #7**.
- Multi-tenant key scoping (per-Project) → v1.1 (Backlog).
- `rotate_own_key` → users do `create_actor` + `revoke_api_key` separately.
- Audit-log browser Web view → **cap #11 explicitly says no Web audit browser; CLI/MCP/REST only.**
- Four read-only views (Pipelines, Workflows, ADRs, Learnings) → **cap #11**.
- Audit JSONB payload truncation (>50KB cap) → **cap #3+ followup**.
- Argon2 timing-attack resistance assertions → not a meaningful security property at v1 scale.
- mTLS / JWT / OAuth → not in v1 (trusted single-instance).

---

## Implementation order

```
[hard preconditions: AQ2-15 + AQ2-18-amendment merged on main; AQ2-12/13/14 already merged]
                          ↓
Story 2.1 — Postgres + SA + Alembic scaffold (api boot runs migrations)
Story 2.2 — Schema migration: actors/api_keys/audit_log
Story 2.3 — Pydantic contract models
Story 2.4 — Service layer + Bearer auth (REST + MCP HTTP) — corrected model
Story 2.5 — Audit writer (same-tx, mutation-only, redactor)
                          ─── C1 (auth + audit primitives live with tests) ───
Story 2.6 — setup op (auditless; advisory-lock first-run)
Story 2.7 — whoami / list_actors / create_actor (REST + CLI + MCP HTTP)
Story 2.8 — revoke_api_key (own-key-only; 403 + audit; last-key 409; race)
Story 2.9 — query_audit_log (REST + CLI + MCP; SQL-injection probes)
Story 2.10 — MCP agent_identity informational field
Story 2.11 — Web /login + /logout + /whoami panel
Story 2.12 — Parity tests + CI workflow updates + atomicity + redact-evidence.sh
                          ─── C2 (full Docker stack + atomicity green + redacted) ───
Run scripts/validate-cap02.sh end-to-end
Commit redacted artifacts under plans/v2-rebuild/artifacts/cap-02/
Open ONE PR for the whole capability
                          ─── C3 (PR opened — await Ghost merge approval) ───
Ghost re-audits → approves merge → Codex squash-merges
Submit cap-#2 outcome on the cap-#2 epic per ADR-AQ-030
Mark capability #2 [DONE] in capabilities.md
Ghost approves cap #3 to start
```

---

## Plane ticket creation guidance

**Epic:** `Capability #2: Authenticated Actors + Bearer auth + same-transaction audit log`. Labels: `cap:02`, `kind:epic`. Parent of 12 child stories.

**Plus AQ2-18 amendment** is filed FIRST (separate ticket) and merged before cap #2 begins.

**Stories** (one per AQ2-S1..S12):

| Story | Plane title | Labels |
|---|---|---|
| 2.1 | `[Story 2.1] Postgres + SQLAlchemy + Alembic scaffold (with api-boot migration)` | `cap:02, kind:plan-story, area:docker, area:api` |
| 2.2 | `[Story 2.2] Schema migration: actors, api_keys, audit_log` | `cap:02, kind:plan-story, area:api` |
| 2.3 | `[Story 2.3] Pydantic contract models for auth + audit` | `cap:02, kind:plan-story, area:contract` |
| 2.4 | `[Story 2.4] Service layer + Bearer auth (REST + MCP HTTP) — corrected model` | `cap:02, kind:plan-story, area:api, area:security` |
| 2.5 | `[Story 2.5] Audit writer (same-tx, mutation-only, recursive redactor)` | `cap:02, kind:plan-story, area:api, area:security` |
| 2.6 | `[Story 2.6] setup op (auditless; advisory-lock first-run)` | `cap:02, kind:plan-story, area:api, area:cli, area:security` |
| 2.7 | `[Story 2.7] whoami / list_actors / create_actor (REST+CLI+MCP)` | `cap:02, kind:plan-story, area:api, area:cli, area:mcp` |
| 2.8 | `[Story 2.8] revoke_api_key (own-key-only; race-safe)` | `cap:02, kind:plan-story, area:api, area:cli, area:mcp, area:security` |
| 2.9 | `[Story 2.9] query_audit_log (REST+CLI+MCP)` | `cap:02, kind:plan-story, area:api, area:cli, area:mcp` |
| 2.10 | `[Story 2.10] MCP agent_identity informational field` | `cap:02, kind:plan-story, area:mcp, area:security` |
| 2.11 | `[Story 2.11] Web /login + /logout + /whoami panel` | `cap:02, kind:plan-story, area:web, area:security` |
| 2.12 | `[Story 2.12] Parity tests + CI workflows + atomicity + redact-evidence` | `cap:02, kind:plan-story, area:contract, area:ci, area:security` |

---

## Critical files

**Reused from cap #1** (read-only references):
- `apps/api/src/aq_api/_datetime.py` (parse_utc)
- `apps/api/src/aq_api/_version.py` (env-driven loader pattern)
- `apps/api/src/aq_api/_health.py` (factory pattern)
- `apps/api/src/aq_api/models/health.py` (Pydantic v2 frozen + extra=forbid + field_validator)
- `apps/api/src/aq_api/app.py` (FastAPI wiring; gains auth + DB)
- `apps/api/src/aq_api/mcp.py` (FastMCP tool decorators; gains auth + agent_identity)
- `apps/cli/src/aq_cli/main.py` (Typer + httpx; gains _post, _delete, subcommands)
- `apps/web/app/api/health/route.ts` (proxy pattern)
- `tests/parity/conftest.py` (extend with db_url + truncate_db + redact_evidence)
- `tests/parity/test_four_surface_parity.py` (extend, don't replace)

**New files in cap #2:**
- `apps/api/src/aq_api/_db.py`, `_settings.py`, `_auth.py`, `_audit.py`, `_request_context.py`
- `apps/api/src/aq_api/services/auth.py`, `services/setup.py`, `services/actors.py`, `services/api_keys.py`, `services/audit.py`
- `apps/api/src/aq_api/models/db.py`, `models/auth.py`, `models/audit.py`
- `apps/api/src/aq_api/routes/setup.py`, `routes/actors.py`, `routes/api_keys.py`, `routes/audit.py`
- `apps/api/alembic.ini`, `apps/api/alembic/env.py`, `apps/api/alembic/script.py.mako`
- `apps/api/alembic/versions/0001_initial.py`, `0002_actors_apikeys_audit.py`
- `apps/cli/src/aq_cli/_config.py`
- `apps/web/app/lib/session.ts`
- `apps/web/app/login/page.tsx`, `app/login/route.ts`, `app/logout/route.ts`
- `apps/web/app/whoami/page.tsx`
- `apps/web/app/api/actors/me/route.ts`
- `apps/web/e2e/auth.spec.ts`
- `tests/parity/test_audit_atomicity.py`
- `scripts/validate-cap02.sh`, `scripts/validate-cap02.ps1`, `scripts/redact-evidence.sh`

**Configuration changes:**
- `docker-compose.yml` — `db` service, env mappings (POSTGRES_PASSWORD, AQ_SESSION_SECRET, AQ_COOKIE_SECURE)
- `apps/api/pyproject.toml` — SA + asyncpg + psycopg + alembic + argon2-cffi + pydantic-settings
- `apps/api/Dockerfile` — entrypoint runs `alembic upgrade head` before uvicorn
- `apps/web/package.json` — iron-session
- Root `.env.example` — document new env vars
- `.github/workflows/test.yml` + `parity.yml` — Postgres service + new env

---

## Verification (proof this capability shipped)

The submission payload references every DoD id (DOD-CAP02-01..15 plus 12 story DoDs) and points each at a real **redacted** artifact. The `dod_results[]` array has all entries with `status="passed"`. The `commands_run[]` array contains the full `validate-cap02.sh` real exit codes. The `files_changed[]` array lists every file under "Critical files" plus tests + redacted artifacts. The artifacts directory `plans/v2-rebuild/artifacts/cap-02/` contains all referenced files, all of which pass `gitleaks` and `redact-evidence.sh`. `risks_or_deviations` lists every item from the Risks section. `handoff` names cap #3 (Project / Workflow / Pipeline / Job entities).

If any DoD fails, capability #2 is **not** marked `[DONE]`. Failed DoDs are surfaced with `status="failed"` + `failure_reason` + `next_action` per ADR-AQ-030. Capability #3 does not start.
