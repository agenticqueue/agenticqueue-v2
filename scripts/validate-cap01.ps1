$ErrorActionPreference = "Stop"

$ArtifactDir = "plans/v2-rebuild/artifacts/cap-01"
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null

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

docker compose build 2>&1 |
    Tee-Object -FilePath "$ArtifactDir/docker-build.txt"
docker compose up -d 2>&1 |
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

Push-Location apps/web
pnpm playwright test e2e/health.spec.ts
Pop-Location

curl.exe -s http://localhost:8001/openapi.json |
    jq . |
    Out-File "$ArtifactDir/openapi.json" -Encoding utf8
git diff --no-index -- "$ArtifactDir/openapi.json" tests/parity/openapi.snapshot.json

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
