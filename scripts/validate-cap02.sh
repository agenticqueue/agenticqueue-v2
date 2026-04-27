#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_DIR="plans/v2-rebuild/artifacts/cap-02"
ENV_FILE="${AQ_CAP02_ENV_FILE:-.env.cap02.local}"
PROJECT="${AQ_COMPOSE_PROJECT:-aq2cap02}"
POSTGRES_DB_NAME="${POSTGRES_DB:-aq2_test}"

mkdir -p "$ARTIFACT_DIR"

random_hex() {
  python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
}

if [ ! -f "$ENV_FILE" ]; then
  POSTGRES_PASSWORD_VALUE="$(random_hex)"
  KEY_LOOKUP_SECRET_VALUE="$(random_hex)"
  SESSION_SECRET_VALUE="$(random_hex)"
  {
    echo "POSTGRES_DB=${POSTGRES_DB_NAME}"
    echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD_VALUE}"
    echo "DATABASE_URL=postgresql+asyncpg://aq:${POSTGRES_PASSWORD_VALUE}@db:5432/${POSTGRES_DB_NAME}"
    echo "DATABASE_URL_SYNC=postgresql+psycopg://aq:${POSTGRES_PASSWORD_VALUE}@db:5432/${POSTGRES_DB_NAME}"
    echo "AQ_KEY_LOOKUP_SECRET=${KEY_LOOKUP_SECRET_VALUE}"
    echo "AQ_SESSION_SECRET=${SESSION_SECRET_VALUE}"
    echo "AQ_COOKIE_SECURE=false"
    echo "AQ_API_URL=http://api:8000"
  } > "$ENV_FILE"
fi

export AQ_VERSION="${AQ_VERSION:-0.0.0-dev}"
export AQ_GIT_COMMIT="${AQ_GIT_COMMIT:-$(git rev-parse --short HEAD)}"
export AQ_BUILT_AT="${AQ_BUILT_AT:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"

compose() {
  docker compose --env-file "$ENV_FILE" -p "$PROJECT" "$@"
}

truncate_db() {
  compose exec -T db psql \
    -U aq \
    -d "$POSTGRES_DB_NAME" \
    -v ON_ERROR_STOP=1 \
    -c "DELETE FROM audit_log; DELETE FROM api_keys; DELETE FROM actors;" >/dev/null
}

mint_founder_key() {
  python - <<'PY'
import json
import urllib.request

request = urllib.request.Request(
    "http://localhost:8001/setup",
    data=b"{}",
    headers={"content-type": "application/json"},
    method="POST",
)
payload = json.loads(urllib.request.urlopen(request).read())
print(payload["founder_key"])
PY
}

compose down --remove-orphans
compose build --no-cache 2>&1 | tee "$ARTIFACT_DIR/docker-build.txt"
compose up -d --wait 2>&1 | tee "$ARTIFACT_DIR/docker-up.txt"
compose ps 2>&1 | tee "$ARTIFACT_DIR/docker-health.txt"

{
  echo "## alembic downgrade base"
  compose exec -T api uv run --frozen --no-dev alembic -c apps/api/alembic.ini downgrade base
  echo "## alembic upgrade head"
  compose exec -T api uv run --frozen --no-dev alembic -c apps/api/alembic.ini upgrade head
} 2>&1 | tee "$ARTIFACT_DIR/alembic-cap02-down-up.txt"

truncate_db
FOUNDER_KEY="$(mint_founder_key)"
export AQ_WEB_TEST_KEY="$FOUNDER_KEY"

python - <<'PY' > "$ARTIFACT_DIR/commit-matches-head.txt"
import json
import os
import subprocess
import urllib.request

sha = subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"],
    capture_output=True,
    check=True,
    text=True,
).stdout.strip()
request = urllib.request.Request(
    "http://localhost:8001/version",
    headers={"Authorization": f"Bearer {os.environ['AQ_WEB_TEST_KEY']}"},
)
version = json.loads(urllib.request.urlopen(request).read())
assert version["commit"] == sha, f"commit mismatch: {version['commit']} vs {sha}"
print(f"COMMIT_MATCHES_HEAD {sha}")
PY

{
  echo "## ruff"
  uv run ruff check .
  echo "## mypy"
  uv run mypy --strict apps/api/src/aq_api/ apps/cli/src/aq_cli/
  echo "## host api+cli pytest"
  uv run pytest apps/api/tests apps/cli/tests -q
  echo "## docker api+cli pytest"
  compose exec -T api uv run --group dev pytest apps/api/tests apps/cli/tests -q
} 2>&1 | tee "$ARTIFACT_DIR/story-2-12-python-checks.txt"

truncate_db
FOUNDER_KEY="$(mint_founder_key)"
export AQ_WEB_TEST_KEY="$FOUNDER_KEY"

pnpm --filter @agenticqueue/web exec playwright install chromium >/dev/null
AQ_API_URL=http://localhost:8001 \
AQ_COOKIE_SECURE=false \
PLAYWRIGHT_USE_DOCKER=1 \
pnpm --filter @agenticqueue/web exec playwright test \
  e2e/health.spec.ts \
  e2e/auth.spec.ts \
  --reporter=line 2>&1 | tee "$ARTIFACT_DIR/story-2-12-web-checks.txt"

AQ_API_URL=http://localhost:8001 \
AQ_WEB_URL=http://127.0.0.1:3002 \
AQ_COMPOSE_ENV_FILE="$ENV_FILE" \
AQ_COMPOSE_PROJECT="$PROJECT" \
POSTGRES_DB="$POSTGRES_DB_NAME" \
PLAYWRIGHT_USE_DOCKER=1 \
uv run pytest tests/parity/test_four_surface_parity.py \
  --junit-xml="$ARTIFACT_DIR/parity-test-report.xml" \
  2>&1 | tee "$ARTIFACT_DIR/parity-test-output.txt"

compose exec -T api rm -rf /app/tests
compose cp tests api:/app/tests >/dev/null
compose exec -T api uv run --group dev pytest \
  tests/parity/test_audit_atomicity.py \
  --junit-xml=/tmp/atomicity-test-report.xml \
  2>&1 | tee "$ARTIFACT_DIR/atomicity-test-output.txt"
compose cp api:/tmp/atomicity-test-report.xml "$ARTIFACT_DIR/atomicity-test-report.xml" >/dev/null

uv run pytest tests/parity/test_redactor.py \
  --junit-xml="$ARTIFACT_DIR/redactor-test.xml" \
  2>&1 | tee "$ARTIFACT_DIR/redactor-pass.txt"

printf 'founder_key=aq2_%s\nkey_hash=$argon2id$v=19$m=65536,t=2,p=2$abcdef$abcdef\nsecret=%s\n' \
  "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" \
  "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" \
  | scripts/redact-evidence.sh > "$ARTIFACT_DIR/redactor-sanity.txt"

{
  for workflow in lint.yml test.yml build.yml parity.yml; do
    echo "## ${workflow}"
    gh run list --workflow="$workflow" --limit=1 --json url,status,conclusion 2>/dev/null \
      || echo "CI_RUN_PENDING_OR_GH_UNAVAILABLE"
  done
} > "$ARTIFACT_DIR/ci-run.txt"

scripts/redact-evidence.sh "$ARTIFACT_DIR"/*

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --source "$ARTIFACT_DIR" --no-git --no-banner --redact --exit-code 1 \
    2>&1 | tee "$ARTIFACT_DIR/gitleaks-pass.txt"
else
  docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.21.2 detect \
    --source /repo/"$ARTIFACT_DIR" \
    --no-git \
    --no-banner \
    --redact \
    --exit-code 1 \
    2>&1 | tee "$ARTIFACT_DIR/gitleaks-pass.txt"
fi

scripts/redact-evidence.sh "$ARTIFACT_DIR"/*

echo "VALIDATE_CAP02_OK"
