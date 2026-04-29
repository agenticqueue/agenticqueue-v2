# Cap #4 C2 evidence summary

Story: AQ2-69 - Evidence pack + capabilities.md fix-up.
Branch: aq2-cap-04.
Base: main at c956f1d.
Current pre-commit HEAD when evidence was generated: 6c0e73b.

## Verification matrix

- Pytest matrix command: docker compose exec -T api uv run pytest -q apps/api/tests apps/cli/tests tests/parity tests/atomicity
- Pytest result tail:

```text
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastmcp/server/auth/providers/jwt.py:10
  /app/.venv/lib/python3.12/site-packages/fastmcp/server/auth/providers/jwt.py:10: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
  It will be compatible before version 2.0.0.
    from authlib.jose import JsonWebKey, JsonWebToken

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
199 passed, 6 skipped, 1 warning in 88.23s (0:01:28)
```

- Mypy command: docker compose exec -T api uv run mypy --strict apps/api/src/aq_api/
- Mypy result: Success: no issues found in 48 source files
- Ruff command: docker compose exec -T api uv run ruff check apps/api apps/cli
- Ruff result: All checks passed!

## Capabilities fix-up

plans/v2-rebuild/capabilities.md now reflects cap #3.5 and cap #4 locked decisions:

- cap #3.5 template and cloned Jobs are ready, not draft; queue operations exclude templates via the pipelines join and p.is_template=false.
- cap #4 CLI names are aq job claim, aq job release, aq job reset-claim, aq job heartbeat; no top-level claim/release shorthand remains.
- successful heartbeat_job is not audited; heartbeat denials are audited.
- auto-release writes a successful claim_auto_release audit row with target_kind=job, target_id=job_id, sweeper actor, and error_code=lease_expired.
- FastMCP server instructions use the installed 2.14.7 constructor path FastMCP(..., instructions=...) rather than a nonexistent set_instructions(...) method.
- stale Contract Profile discovery / describe_contract_profile guidance is removed; agents read the Job inline contract field.

## Evidence artifacts

- final-test-matrix.txt: full Docker pytest matrix.
- final-mypy-strict.txt: strict API typing.
- final-ruff.txt: API/CLI lint.
- final-db-shape.txt: jobs/pipelines shape, indexes, active sweeper count, draft Job count.
- capabilities-md-greps.txt: stale-string and replacement-string evidence for the plan fix-up.
- cap04-locks-grep.txt: source grep evidence for SKIP LOCKED, queue exclusion, env vars, heartbeat audit skip, auto-release error code, FastMCP instructions, race and atomicity tests.
- final-explain-claim-no-label.txt: claim query no-label EXPLAIN.
- final-explain-claim-with-labels.txt: claim query label-filter EXPLAIN showing idx_jobs_labels_gin + idx_jobs_state_project_created.
- final-explain-sweeper-stale-claim.txt: sweeper stale-claim EXPLAIN showing idx_jobs_in_progress_heartbeat.
- plane-gap-tickets-folded.txt: AQ2-16/AQ2-17 folded comments and AQ2-70/AQ2-71 status.

## Known carry-overs

- AQ2-70 test-hygiene/dev-DB cleanup is intentionally paused until cap-4 PR merge. The patch is preserved in stash outside this Story 4.7 commit.
- Dev DB contains disposable test/evidence rows from cap-4 audits and race tests. C2 database shape remains valid: exactly one active aq-system-sweeper; zero draft Jobs; no in-progress Jobs after verification.
