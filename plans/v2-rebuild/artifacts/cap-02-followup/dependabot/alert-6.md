# Alert #6 - pytest has vulnerable tmpdir handling

- Severity: MODERATE
- Dependency: pytest
- Ecosystem: pip
- Manifest: uv.lock
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/6
- GHSA advisory: https://github.com/advisories/GHSA-6w46-j5rx-g56g
- Vulnerable range: < 9.0.3
- First patched version: 9.0.3

## Audit Method

`pytest` is a direct root dev dependency. The root dependency floor was tightened from `pytest>=8.3,<10` to `pytest>=9.0.3,<10`, and `uv lock` refreshed the lockfile to `pytest v9.0.3`.

Diff stat:

```text
 pyproject.toml |  2 +-  uv.lock        | 17 +++++++++--------  2 files changed, 10 insertions(+), 9 deletions(-)
```

## Verification

The full no-cache Docker matrix output is committed at `plans/v2-rebuild/artifacts/cap-02-followup/dependabot/docker-matrix-final.txt`.

Docker stack commands completed with exit 0:

```text
===== docker compose down =====
COMMAND: docker compose --env-file .env.cap02.local down --remove-orphans
EXIT_CODE: 0
...
===== docker compose down =====
COMMAND: docker compose --env-file .env.cap02.local down --remove-orphans
EXIT_CODE: 0
===== docker compose build --no-cache =====
COMMAND: docker compose --env-file .env.cap02.local build --no-cache
#1 [internal] load local bake definitions
#1 reading from stdin 1.20kB done
#1 DONE 0.0s
...
===== docker compose up -d --wait =====
COMMAND: docker compose --env-file .env.cap02.local up -d --wait
EXIT_CODE: 0
```

Required test output, verbatim:

```text
===== api pytest =====
COMMAND: docker compose --env-file .env.cap02.local exec -T api uv run pytest -q apps/api/tests apps/cli/tests
........................................................................ [ 96%]
...                                                                      [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastmcp/server/auth/providers/jwt.py:10
  /app/.venv/lib/python3.12/site-packages/fastmcp/server/auth/providers/jwt.py:10: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
  It will be compatible before version 2.0.0.
    from authlib.jose import JsonWebKey, JsonWebToken

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
75 passed, 1 warning in 22.80s
EXIT_CODE: 0
===== api ruff =====
COMMAND: docker compose --env-file .env.cap02.local exec -T api uv run ruff check apps/api apps/cli
All checks passed!
EXIT_CODE: 0
===== api mypy =====
COMMAND: docker compose --env-file .env.cap02.local exec -T api uv run mypy --strict apps/api/src/aq_api/
Success: no issues found in 27 source files
EXIT_CODE: 0
```

## Decision

Bumped to patched version `pytest 9.0.3`.

GitHub API note: AQ2-38 requested dismissal reason `fixed`, but the Dependabot REST API rejects that value. The API accepts `fix_started`, so this alert was dismissed with `fix_started` and will be closed by GitHub when the PR merges.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/6

Dismissal comment:

```text
Fix started on branch aq2-cap-02-dependabot-triage by bumping pytest to 9.0.3. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-6.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"Fix started on branch aq2-cap-02-dependabot-triage by bumping pytest to 9.0.3. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-6.md","dismissed_reason":"fix_started","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/6","number":6,"state":"dismissed"}
```
