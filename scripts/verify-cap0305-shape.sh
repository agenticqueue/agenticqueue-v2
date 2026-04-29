#!/usr/bin/env bash
set -euo pipefail

DB_USER="${POSTGRES_USER:-aq}"
DB_NAME="${POSTGRES_DB:-aq2}"

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

echo "# cap0305 DB shape verification"
echo "database=$DB_NAME user=$DB_USER"

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
  "jobs legacy columns absent" \
  "NONE" \
  "SELECT COALESCE(string_agg(column_name, ',' ORDER BY column_name), 'NONE')
     FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'jobs'
      AND column_name IN ('instantiated_from_step_id', 'contract_profile_id');"

assert_eq \
  "pipelines legacy columns absent" \
  "NONE" \
  "SELECT COALESCE(string_agg(column_name, ',' ORDER BY column_name), 'NONE')
     FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'pipelines'
      AND column_name IN (
        'instantiated_from_workflow_id',
        'instantiated_from_workflow_version'
      );"

assert_eq \
  "jobs contract column present" \
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
  "ship-a-thing template pipeline count" \
  "1" \
  "SELECT count(*)::text
     FROM pipelines
    WHERE is_template = true
      AND name = 'ship-a-thing';"

assert_eq \
  "ship-a-thing template ready jobs with contracts" \
  "3" \
  "SELECT count(*)::text
     FROM jobs j
     JOIN pipelines p ON p.id = j.pipeline_id
    WHERE p.is_template = true
      AND p.name = 'ship-a-thing'
      AND j.state = 'ready'
      AND j.contract != '{}'::jsonb
      AND jsonb_array_length(j.contract->'dod_items') > 0;"

assert_eq \
  "cap03 no draft jobs invariant" \
  "0" \
  "SELECT count(*)::text FROM jobs WHERE state = 'draft';"

assert_eq \
  "job_edges edge_type check shape" \
  "ok" \
  "SELECT CASE
      WHEN constraint_def LIKE '%gated_on%'
       AND constraint_def LIKE '%parent_of%'
       AND constraint_def LIKE '%sequence_next%'
       AND constraint_def NOT LIKE '%instantiated_from%'
       AND constraint_def NOT LIKE '%job_references%'
      THEN 'ok'
      ELSE constraint_def
    END
   FROM (
     SELECT COALESCE(pg_get_constraintdef(oid), 'missing') AS constraint_def
       FROM pg_constraint
      WHERE conname = 'job_edges_edge_type_check'
   ) AS constraint_shape;"

if [[ "$failures" -ne 0 ]]; then
  echo "cap0305 DB shape verification failed: $failures check(s) failed"
  exit 1
fi

echo "cap0305 DB shape verification passed"
