$ErrorActionPreference = "Stop"

$ArtifactDir = "plans/v2-rebuild/artifacts/cap-01"
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null

if (-not $env:AQ_VERSION) {
    $env:AQ_VERSION = "0.0.0-dev"
}
if (-not $env:AQ_GIT_COMMIT) {
    $env:AQ_GIT_COMMIT = (git rev-parse --short HEAD).Trim()
}
if (-not $env:AQ_BUILT_AT) {
    $env:AQ_BUILT_AT = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
}

$scaffold = @()
$requiredFiles = @(
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "pnpm-workspace.yaml",
    "pnpm-lock.yaml",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    ".editorconfig",
    ".gitattributes",
    "LICENSE",
    "README.md",
    "AUTHORS.md",
    ".github/CODEOWNERS",
    "tests/parity/openapi.snapshot.json",
    "tests/parity/mcp_schema.snapshot.json",
    "scripts/validate-cap01.sh",
    "scripts/validate-cap01.ps1"
)

foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file -PathType Leaf)) {
        throw "Missing required file: $file"
    }
    $scaffold += "ok $file"
}

if (-not ((Get-Content LICENSE -First 1) -match "Copyright 2026 Mario Watson")) {
    throw "LICENSE attribution missing"
}
if (-not ((Get-Content README.md -First 3 | Out-String) -match "Mario Watson")) {
    throw "README attribution missing"
}
if (-not ((Get-Content .github/CODEOWNERS | Out-String) -match "@mario-watson")) {
    throw "CODEOWNERS missing @mario-watson"
}

$scaffold += "SCAFFOLD_OK"
$scaffold | Tee-Object -FilePath "$ArtifactDir/scaffold-validation.txt"

git log --oneline -10 | Out-File "$ArtifactDir/git-shas.txt" -Encoding utf8

uv run mypy apps/api/src/aq_api/models/ |
    Tee-Object -FilePath "$ArtifactDir/typecheck.txt"

docker compose down --remove-orphans
docker compose build --no-cache 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/docker-build.txt"
docker compose up -d --wait 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/docker-up.txt"

while ($true) {
    try {
        Invoke-WebRequest -UseBasicParsing http://localhost:8001/healthz | Out-Null
        break
    }
    catch {
        Start-Sleep -Seconds 1
    }
}
"API healthy at $((Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"))" |
    Tee-Object -FilePath "$ArtifactDir/docker-healthcheck.txt"

@"
import json
import subprocess
import urllib.request

sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
version = json.loads(urllib.request.urlopen("http://localhost:8001/version").read())
assert version["commit"] == sha, f"commit mismatch: {version['commit']} vs {sha}"
print(f"COMMIT_MATCHES_HEAD {sha}")
"@ | python -

curl.exe -i http://localhost:8001/healthz |
    Tee-Object -FilePath "$ArtifactDir/rest-healthz.txt"
curl.exe -i http://localhost:8001/version |
    Tee-Object -FilePath "$ArtifactDir/rest-version.txt"

$env:AQ_API_URL = "http://localhost:8001"
uv run aq health | Tee-Object -FilePath "$ArtifactDir/cli-health.txt"
uv run aq version | Tee-Object -FilePath "$ArtifactDir/cli-version.txt"

uv run python -m tests.parity.mcp_harness health_check |
    Tee-Object -FilePath "$ArtifactDir/mcp-health.txt"
uv run python -m tests.parity.mcp_harness get_version |
    Tee-Object -FilePath "$ArtifactDir/mcp-version.txt"

$env:PLAYWRIGHT_USE_DOCKER = "1"
pnpm --filter "@agenticqueue/web" exec playwright test e2e/health.spec.ts

@"
import json
import urllib.request

payload = json.loads(urllib.request.urlopen("http://localhost:8001/openapi.json").read())
print(json.dumps(payload, indent=2))
"@ | python - | Out-File "$ArtifactDir/openapi.json" -Encoding utf8
$openapiDiff = git diff --no-index -- "$ArtifactDir/openapi.json" tests/parity/openapi.snapshot.json 2>&1
if ($LASTEXITCODE -eq 0) {
    "OPENAPI_DIFF_EMPTY" | Out-File "$ArtifactDir/openapi-diff.txt" -Encoding utf8
}
else {
    $openapiDiff | Out-File "$ArtifactDir/openapi-diff.txt" -Encoding utf8
    exit $LASTEXITCODE
}

uv run pytest tests/parity/ --junit-xml="$ArtifactDir/parity-test-report.xml"
uv run python tests/parity/four_surface_diff.py |
    Out-File "$ArtifactDir/four-surface-equivalence.txt" -Encoding utf8

@(
    "## lint"
    gh run list --workflow=lint.yml --limit=1 --json url,status,conclusion
    "## test"
    gh run list --workflow=test.yml --limit=1 --json url,status,conclusion
    "## build"
    gh run list --workflow=build.yml --limit=1 --json url,status,conclusion
    "## parity"
    gh run list --workflow=parity.yml --limit=1 --json url,status,conclusion
) | Out-File "$ArtifactDir/ci-run.txt" -Encoding utf8
