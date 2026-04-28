# Cap 0305 Checkpoint Evidence Summary

AQ2-59 captures the cap #3.5 checkpoint evidence. This pack verifies the
schema consolidation, fresh-install setup path, existing dev DB migration path,
test matrix, DB shape, and secret scan.

## Evidence Files

- `full-test-matrix.txt` - Docker pytest, mypy strict, and ruff output.
- `migration-existing-db.txt` - existing dev DB Alembic upgrade/current,
  downgrade/upgrade round-trip, and post-round-trip shape verification.
- `migration-fresh-db.txt` - isolated `aq2_freshmigration_test` database
  creation, fresh Alembic upgrade, pre-setup empty-template check, isolated
  `aq setup`, seeded template verification, and isolated DB cleanup.
- `db-shape-verification.txt` - `scripts/verify-cap0305-shape.sh` output
  against the dev DB.
- `seed-template-pipeline.txt` - `ship-a-thing` template Pipeline and its
  three ready Jobs with non-empty Contract JSONB.
- `gitleaks-pass.txt` - gitleaks v8.30.1 command evidence.

## Locked Corrections Applied

- Alembic uses `-c apps/api/alembic.ini` because the API container working
  directory is `/app`.
- Fresh DB evidence uses an isolated test database, not `docker compose down -v`.
- Template Jobs are `state='ready'`; cap #3.5 preserves the cap #3 invariant
  that no Jobs enter `draft`.
- `job_edges_edge_type_check` remains the three-value cap #3 shape:
  `gated_on`, `parent_of`, `sequence_next`.

## Stop Point

After this evidence commit is pushed, cap #3.5 stops for claude checkpoint
audit and Mario approval. No PR is opened here, and AQ2-47 is not claimed until
the checkpoint is approved.
