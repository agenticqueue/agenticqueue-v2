$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$ArtifactDir = "plans/v2-rebuild/artifacts/cap-02"
$EnvFile = if ($env:AQ_CAP02_ENV_FILE) { $env:AQ_CAP02_ENV_FILE } else { ".env.cap02.local" }
$Project = if ($env:AQ_COMPOSE_PROJECT) { $env:AQ_COMPOSE_PROJECT } else { "aq2cap02" }
$PostgresDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "aq2_test" }

New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null

function New-HexSecret {
    param([int] $Bytes = 32)
    $buffer = [byte[]]::new($Bytes)
    [Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToHexString($buffer).ToLowerInvariant()
}

if (-not (Test-Path $EnvFile -PathType Leaf)) {
    $postgresPassword = New-HexSecret
    $keyLookupSecret = New-HexSecret
    $sessionSecret = New-HexSecret
    @(
        "POSTGRES_DB=$PostgresDb"
        "POSTGRES_PASSWORD=$postgresPassword"
        "DATABASE_URL=postgresql+asyncpg://aq:$postgresPassword@db:5432/$PostgresDb"
        "DATABASE_URL_SYNC=postgresql+psycopg://aq:$postgresPassword@db:5432/$PostgresDb"
        "AQ_KEY_LOOKUP_SECRET=$keyLookupSecret"
        "AQ_SESSION_SECRET=$sessionSecret"
        "AQ_COOKIE_SECURE=false"
        "AQ_API_URL=http://api:8000"
    ) | Set-Content -Path $EnvFile -Encoding ascii
}

if (-not $env:AQ_VERSION) {
    $env:AQ_VERSION = "0.0.0-dev"
}
if (-not $env:AQ_GIT_COMMIT) {
    $env:AQ_GIT_COMMIT = (git rev-parse --short HEAD).Trim()
}
if (-not $env:AQ_BUILT_AT) {
    $env:AQ_BUILT_AT = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
}

function Invoke-Compose {
    docker compose --env-file $EnvFile -p $Project @args
}

function Invoke-TruncateDb {
    Invoke-Compose exec -T db psql -U aq -d $PostgresDb -v ON_ERROR_STOP=1 -c "DELETE FROM audit_log; DELETE FROM api_keys; DELETE FROM actors;" | Out-Null
}

function New-FounderKey {
    $script = @"
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
"@
    return (($script | python -) | Select-Object -First 1).Trim()
}

Invoke-Compose down --remove-orphans
Invoke-Compose build --no-cache 2>&1 | Tee-Object -FilePath "$ArtifactDir/docker-build.txt"
Invoke-Compose up -d --wait 2>&1 | Tee-Object -FilePath "$ArtifactDir/docker-up.txt"
Invoke-Compose ps 2>&1 | Tee-Object -FilePath "$ArtifactDir/docker-health.txt"

@(
    "## alembic downgrade base"
    Invoke-Compose exec -T api uv run --frozen --no-dev alembic -c apps/api/alembic.ini downgrade base
    "## alembic upgrade head"
    Invoke-Compose exec -T api uv run --frozen --no-dev alembic -c apps/api/alembic.ini upgrade head
) 2>&1 | Tee-Object -FilePath "$ArtifactDir/alembic-cap02-down-up.txt"

Invoke-TruncateDb
$env:AQ_WEB_TEST_KEY = New-FounderKey

@"
import json
import os
import subprocess
import urllib.request

sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, check=True, text=True).stdout.strip()
request = urllib.request.Request(
    "http://localhost:8001/version",
    headers={"Authorization": f"Bearer {os.environ['AQ_WEB_TEST_KEY']}"},
)
version = json.loads(urllib.request.urlopen(request).read())
assert version["commit"] == sha, f"commit mismatch: {version['commit']} vs {sha}"
print(f"COMMIT_MATCHES_HEAD {sha}")
"@ | python - | Out-File "$ArtifactDir/commit-matches-head.txt" -Encoding utf8

@(
    "## ruff"
    uv run ruff check .
    "## mypy"
    uv run mypy --strict apps/api/src/aq_api/ apps/cli/src/aq_cli/
    "## host api+cli pytest"
    uv run pytest apps/api/tests apps/cli/tests -q
    "## docker api+cli pytest"
    Invoke-Compose exec -T api uv run --group dev pytest apps/api/tests apps/cli/tests -q
) 2>&1 | Tee-Object -FilePath "$ArtifactDir/story-2-12-python-checks.txt"

Invoke-TruncateDb
$env:AQ_WEB_TEST_KEY = New-FounderKey

pnpm --filter "@agenticqueue/web" exec playwright install chromium | Out-Null
$env:AQ_API_URL = "http://localhost:8001"
$env:AQ_COOKIE_SECURE = "false"
$env:PLAYWRIGHT_USE_DOCKER = "1"
pnpm --filter "@agenticqueue/web" exec playwright test e2e/health.spec.ts e2e/auth.spec.ts --reporter=line 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/story-2-12-web-checks.txt"

$env:AQ_WEB_URL = "http://127.0.0.1:3002"
$env:AQ_COMPOSE_ENV_FILE = $EnvFile
$env:AQ_COMPOSE_PROJECT = $Project
$env:POSTGRES_DB = $PostgresDb
uv run pytest tests/parity/test_four_surface_parity.py --junit-xml="$ArtifactDir/parity-test-report.xml" 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/parity-test-output.txt"

Invoke-Compose exec -T api rm -rf /app/tests
Invoke-Compose cp tests api:/app/tests | Out-Null
Invoke-Compose exec -T api uv run --group dev pytest tests/parity/test_audit_atomicity.py --junit-xml=/tmp/atomicity-test-report.xml 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/atomicity-test-output.txt"
Invoke-Compose cp api:/tmp/atomicity-test-report.xml "$ArtifactDir/atomicity-test-report.xml" | Out-Null

uv run pytest tests/parity/test_redactor.py --junit-xml="$ArtifactDir/redactor-test.xml" 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/redactor-pass.txt"

"founder_key=aq2_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`nkey_hash=`$argon2id`$v=19`$m=65536,t=2,p=2`$abcdef`$abcdef`nsecret=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB`n" |
    python scripts/redact_evidence.py |
    Out-File "$ArtifactDir/redactor-sanity.txt" -Encoding utf8

@(
    "## lint.yml"
    gh run list --workflow=lint.yml --limit=1 --json url,status,conclusion 2>$null
    "## test.yml"
    gh run list --workflow=test.yml --limit=1 --json url,status,conclusion 2>$null
    "## build.yml"
    gh run list --workflow=build.yml --limit=1 --json url,status,conclusion 2>$null
    "## parity.yml"
    gh run list --workflow=parity.yml --limit=1 --json url,status,conclusion 2>$null
) | Out-File "$ArtifactDir/ci-run.txt" -Encoding utf8

python scripts/redact_evidence.py $ArtifactDir

if (Get-Command gitleaks -ErrorAction SilentlyContinue) {
    gitleaks detect --source "$ArtifactDir" --no-git --no-banner --redact --exit-code 1 2>&1 |
        Tee-Object -FilePath "$ArtifactDir/gitleaks-pass.txt"
}
else {
    docker run --rm -v "${PWD}:/repo" ghcr.io/gitleaks/gitleaks:v8.21.2 detect --source "/repo/$ArtifactDir" --no-git --no-banner --redact --exit-code 1 2>&1 |
        Tee-Object -FilePath "$ArtifactDir/gitleaks-pass.txt"
}

python scripts/redact_evidence.py $ArtifactDir

"VALIDATE_CAP02_OK"
