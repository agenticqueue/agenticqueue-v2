#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_DIR="${AQ_CAP03_ARTIFACT_DIR:-plans/v2-rebuild/artifacts/cap-03}"
DB_USER="${POSTGRES_USER:-aq}"
DB_NAME="${POSTGRES_DB:-aq2}"

mkdir -p "$ARTIFACT_DIR"

PSQL=(
  docker compose exec -T db
  psql
  -U "$DB_USER"
  -d "$DB_NAME"
  -v ON_ERROR_STOP=1
  -X
  -q
  -t
  -A
)

failures=0

assert_text_eq() {
  local label="$1"
  local expected="$2"
  local actual="$3"

  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS\t%s\t%s\n' "$label" "$actual"
    return
  fi

  printf 'FAIL\t%s\texpected=%s\tactual=%s\n' "$label" "$expected" "$actual"
  failures=$((failures + 1))
}

run_sql() {
  local sql="$1"
  "${PSQL[@]}" -c "$sql" | tr -d '\r' | sed 's/[[:space:]]*$//'
}

assert_eq() {
  local label="$1"
  local expected="$2"
  local sql="$3"
  local actual

  actual="$(run_sql "$sql")"
  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS\t%s\t%s\n' "$label" "$actual"
    return
  fi

  printf 'FAIL\t%s\texpected=%s\tactual=%s\n' "$label" "$expected" "$actual"
  failures=$((failures + 1))
}

{
  echo "# cap03 C2 validation"
  echo "database=$DB_NAME user=$DB_USER"

  mcp_tool_count="$(python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("tests/parity/mcp_schema.snapshot.json").read_text())
print(len(payload["result"]["tools"]))
PY
)"
  assert_text_eq \
    "final cap03 MCP tool count" \
    "29" \
    "$mcp_tool_count"

  assert_eq \
    "workflows table absent" \
    "NULL" \
    "SELECT COALESCE(to_regclass('public.workflows')::text, 'NULL');"

  assert_eq \
    "workflow_steps table absent" \
    "NULL" \
    "SELECT COALESCE(to_regclass('public.workflow_steps')::text, 'NULL');"

  assert_eq \
    "contract_profiles table absent" \
    "NULL" \
    "SELECT COALESCE(to_regclass('public.contract_profiles')::text, 'NULL');"

  assert_eq \
    "jobs inline contract column present" \
    "contract" \
    "SELECT COALESCE(string_agg(column_name, ',' ORDER BY column_name), 'NONE')
       FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'jobs'
        AND column_name = 'contract';"

  assert_eq \
    "pipelines cap0305 columns present" \
    "archived_at,cloned_from_pipeline_id,is_template" \
    "SELECT COALESCE(string_agg(column_name, ',' ORDER BY column_name), 'NONE')
       FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'pipelines'
        AND column_name IN ('is_template', 'cloned_from_pipeline_id', 'archived_at');"

  assert_eq \
    "legacy cap03 columns absent" \
    "NONE" \
    "SELECT COALESCE(string_agg(table_name || '.' || column_name, ',' ORDER BY table_name, column_name), 'NONE')
       FROM information_schema.columns
      WHERE table_schema = 'public'
        AND (
          (table_name = 'jobs' AND column_name IN ('instantiated_from_step_id', 'contract_profile_id'))
          OR (table_name = 'pipelines' AND column_name IN ('instantiated_from_workflow_id', 'instantiated_from_workflow_version'))
        );"

  assert_eq \
    "no draft jobs invariant" \
    "0" \
    "SELECT count(*)::text FROM jobs WHERE state = 'draft';"

  assert_eq \
    "seeded ship-a-thing template count" \
    "1" \
    "SELECT count(*)::text
       FROM pipelines
      WHERE is_template = true
        AND name = 'ship-a-thing';"

  assert_eq \
    "seeded ship-a-thing ready jobs with contracts" \
    "3" \
    "SELECT count(*)::text
       FROM jobs j
       JOIN pipelines p ON p.id = j.pipeline_id
      WHERE p.is_template = true
        AND p.name = 'ship-a-thing'
        AND j.state = 'ready'
        AND j.contract != '{}'::jsonb
        AND jsonb_typeof(j.contract->'dod_items') = 'array'
        AND jsonb_array_length(j.contract->'dod_items') > 0;"

  assert_eq \
    "clone lineage column queryable" \
    "ok" \
    "SELECT CASE
        WHEN EXISTS (
          SELECT 1
            FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'pipelines'
             AND column_name = 'cloned_from_pipeline_id'
        )
        THEN 'ok'
        ELSE 'missing'
      END;"

  assert_eq \
    "job_edges edge_type check excludes instantiated_from" \
    "ok" \
    "SELECT CASE
        WHEN constraint_def LIKE '%gated_on%'
         AND constraint_def LIKE '%parent_of%'
         AND constraint_def LIKE '%sequence_next%'
         AND constraint_def NOT LIKE '%instantiated_from%'
        THEN 'ok'
        ELSE constraint_def
      END
     FROM (
       SELECT COALESCE(pg_get_constraintdef(oid), 'missing') AS constraint_def
         FROM pg_constraint
        WHERE conname = 'job_edges_edge_type_check'
     ) AS constraint_shape;"

  if [[ "$failures" -ne 0 ]]; then
    echo "VALIDATE_CAP03_FAILED failures=$failures"
    exit 1
  fi

  echo "VALIDATE_CAP03_OK"
} | tee "$ARTIFACT_DIR/validate-cap03.txt"
