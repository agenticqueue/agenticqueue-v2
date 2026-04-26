#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_DIR="plans/v2-rebuild/artifacts/cap-01"
mkdir -p "$ARTIFACT_DIR"

export AQ_VERSION="${AQ_VERSION:-0.0.0-dev}"
export AQ_GIT_COMMIT="${AQ_GIT_COMMIT:-$(git rev-parse --short HEAD)}"
export AQ_BUILT_AT="${AQ_BUILT_AT:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"

{
  test -f pyproject.toml && echo "ok pyproject.toml"
  test -f uv.lock && echo "ok uv.lock"
  test -f package.json && echo "ok package.json"
  test -f pnpm-workspace.yaml && echo "ok pnpm-workspace.yaml"
  test -f pnpm-lock.yaml && echo "ok pnpm-lock.yaml"
  test -f docker-compose.yml && echo "ok docker-compose.yml"
  test -f .dockerignore && echo "ok .dockerignore"
  test -f .gitignore && echo "ok .gitignore"
  test -f .editorconfig && echo "ok .editorconfig"
  test -f .gitattributes && echo "ok .gitattributes"
  test -f LICENSE && head -1 LICENSE | grep -q "Copyright 2026 Mario Watson" && echo "ok LICENSE"
  test -f README.md && head -3 README.md | grep -q "Mario Watson" && echo "ok README.md"
  test -f AUTHORS.md && echo "ok AUTHORS.md"
  test -f .github/CODEOWNERS && grep -q "@mario-watson" .github/CODEOWNERS && echo "ok CODEOWNERS"
  test -f tests/parity/openapi.snapshot.json && echo "ok openapi snapshot"
  test -f tests/parity/mcp_schema.snapshot.json && echo "ok mcp schema snapshot"
  test -f scripts/validate-cap01.sh && echo "ok validate-cap01.sh"
  test -f scripts/validate-cap01.ps1 && echo "ok validate-cap01.ps1"
  echo "SCAFFOLD_OK"
} | tee "$ARTIFACT_DIR/scaffold-validation.txt"

git log --oneline -10 > "$ARTIFACT_DIR/git-shas.txt"

uv run mypy apps/api/src/aq_api/models/ \
  | tee "$ARTIFACT_DIR/typecheck.txt"

docker compose down --remove-orphans
docker compose build --no-cache 2>&1 | tee "$ARTIFACT_DIR/docker-build.txt"
docker compose up -d --wait 2>&1 | tee "$ARTIFACT_DIR/docker-up.txt"
until curl -sf http://localhost:8001/healthz; do sleep 1; done
echo "API healthy at $(date -u +%FT%TZ)" \
  | tee "$ARTIFACT_DIR/docker-healthcheck.txt"

python - <<'PY'
import json
import subprocess
import urllib.request

sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
version = json.loads(urllib.request.urlopen("http://localhost:8001/version").read())
assert version["commit"] == sha, f"commit mismatch: {version['commit']} vs {sha}"
print(f"COMMIT_MATCHES_HEAD {sha}")
PY

curl -i http://localhost:8001/healthz \
  | tee "$ARTIFACT_DIR/rest-healthz.txt"
curl -i http://localhost:8001/version \
  | tee "$ARTIFACT_DIR/rest-version.txt"

AQ_API_URL=http://localhost:8001 uv run aq health \
  | tee "$ARTIFACT_DIR/cli-health.txt"
AQ_API_URL=http://localhost:8001 uv run aq version \
  | tee "$ARTIFACT_DIR/cli-version.txt"

uv run python -m tests.parity.mcp_harness health_check \
  | tee "$ARTIFACT_DIR/mcp-health.txt"
uv run python -m tests.parity.mcp_harness get_version \
  | tee "$ARTIFACT_DIR/mcp-version.txt"

PLAYWRIGHT_USE_DOCKER=1 pnpm --filter @agenticqueue/web exec playwright test e2e/health.spec.ts

python - "$ARTIFACT_DIR/openapi.json" <<'PY'
import json
import sys
import urllib.request

payload = json.loads(urllib.request.urlopen("http://localhost:8001/openapi.json").read())
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY
if diff "$ARTIFACT_DIR/openapi.json" tests/parity/openapi.snapshot.json > "$ARTIFACT_DIR/openapi-diff.txt"; then
  echo "OPENAPI_DIFF_EMPTY" > "$ARTIFACT_DIR/openapi-diff.txt"
else
  exit $?
fi

uv run pytest tests/parity/ \
  --junit-xml="$ARTIFACT_DIR/parity-test-report.xml"

uv run python tests/parity/four_surface_diff.py \
  > "$ARTIFACT_DIR/four-surface-equivalence.txt"

{
  echo "## lint"; gh run list --workflow=lint.yml --limit=1 --json url,status,conclusion
  echo "## test"; gh run list --workflow=test.yml --limit=1 --json url,status,conclusion
  echo "## build"; gh run list --workflow=build.yml --limit=1 --json url,status,conclusion
  echo "## parity"; gh run list --workflow=parity.yml --limit=1 --json url,status,conclusion
} > "$ARTIFACT_DIR/ci-run.txt"
