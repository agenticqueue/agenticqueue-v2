import importlib.util
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast

import psycopg
import pytest
from aq_api.models import (
    ClaimNextJobRequest,
    ClaimNextJobResponse,
    ContextPacketStub,
    HeartbeatJobResponse,
    Job,
    ReleaseJobResponse,
    ResetClaimRequest,
    ResetClaimResponse,
)
from psycopg import Connection, sql
from psycopg.errors import UniqueViolation
from pydantic import ValidationError

ACTOR_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
PIPELINE_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
JOB_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
NEXT_JOB_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
PREVIOUS_JOB_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")
NOW = "2026-04-28T16:00:00Z"
LEASE_EXPIRES_AT = "2026-04-28T16:15:00Z"
CONTRACT = {
    "contract_type": "coding-task",
    "dod_items": [{"id": "tests-pass"}],
}
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0006_cap04_indexes_and_system_actor.py"
)
PUBLIC_MODELS = (
    ContextPacketStub,
    ClaimNextJobRequest,
    ClaimNextJobResponse,
    ReleaseJobResponse,
    ResetClaimRequest,
    ResetClaimResponse,
    HeartbeatJobResponse,
)
BASE_SETTINGS = {
    "DATABASE_URL": "postgresql+asyncpg://aq:pw@db:5432/aq2",
    "DATABASE_URL_SYNC": "postgresql+psycopg://aq:pw@db:5432/aq2",
    "POSTGRES_PASSWORD": "pw",
    "AQ_KEY_LOOKUP_SECRET": "test-secret",
}
ORIGINAL_DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")


@pytest.fixture()
def conninfo() -> str:
    if ORIGINAL_DATABASE_URL_SYNC is None:
        pytest.skip("DATABASE_URL_SYNC is required for live actor seed tests")
    return ORIGINAL_DATABASE_URL_SYNC.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )


@pytest.fixture()
def seed_schema(conninfo: str) -> Iterator[str]:
    schema = f"cap04_seed_{uuid.uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE {}.actors (
                        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                        name text NOT NULL,
                        kind text NOT NULL,
                        deactivated_at timestamptz NULL
                    )
                    """
                ).format(sql.Identifier(schema))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE UNIQUE INDEX actors_name_active_uniq
                    ON {}.actors (name)
                    WHERE deactivated_at IS NULL
                    """
                ).format(sql.Identifier(schema))
            )
    try:
        yield schema
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema)
                    )
                )


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cap04_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _job() -> Job:
    return Job(
        id=JOB_ID,
        pipeline_id=PIPELINE_ID,
        project_id=PROJECT_ID,
        state="ready",
        title="Build the thing",
        description="Implement the scoped change",
        contract=CONTRACT,
        labels=["area:web"],
        claimed_by_actor_id=None,
        claimed_at=None,
        claim_heartbeat_at=None,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
    )


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def _settings(**overrides: object):
    os.environ.setdefault("DATABASE_URL", BASE_SETTINGS["DATABASE_URL"])
    os.environ.setdefault("DATABASE_URL_SYNC", BASE_SETTINGS["DATABASE_URL_SYNC"])
    os.environ.setdefault("POSTGRES_PASSWORD", BASE_SETTINGS["POSTGRES_PASSWORD"])
    os.environ.setdefault("AQ_KEY_LOOKUP_SECRET", BASE_SETTINGS["AQ_KEY_LOOKUP_SECRET"])
    from aq_api._settings import Settings

    return Settings(**{**BASE_SETTINGS, **overrides})


def _seed_sql() -> str:
    module = _migration_module()
    return cast(str, module.SYSTEM_ACTOR_SEED_SQL)


def _run_seed(connection: Connection[tuple[object, ...]], schema: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )
        cursor.execute(_seed_sql())


def _active_sweeper_count(
    connection: Connection[tuple[object, ...]],
    schema: str,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT count(*)
                FROM {}.actors
                WHERE name = 'aq-system-sweeper'
                  AND kind = 'script'
                  AND deactivated_at IS NULL
                """
            ).format(sql.Identifier(schema))
        )
        row = cursor.fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def _total_sweeper_count(
    connection: Connection[tuple[object, ...]],
    schema: str,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT count(*)
                FROM {}.actors
                WHERE name = 'aq-system-sweeper'
                """
            ).format(sql.Identifier(schema))
        )
        row = cursor.fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def test_cap04_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    packet = ContextPacketStub(
        project_id=PROJECT_ID,
        pipeline_id=PIPELINE_ID,
        current_job_id=JOB_ID,
    )
    with pytest.raises(ValidationError):
        ContextPacketStub.model_validate(
            {
                **packet.model_dump(),
                "unexpected": "blocked",
            }
        )
    with pytest.raises(ValidationError):
        packet.next_job_id = NEXT_JOB_ID  # type: ignore[misc]


def test_cap04_request_and_response_models_round_trip() -> None:
    packet = ContextPacketStub(
        project_id=PROJECT_ID,
        pipeline_id=PIPELINE_ID,
        current_job_id=JOB_ID,
        previous_jobs=[PREVIOUS_JOB_ID],
        next_job_id=NEXT_JOB_ID,
    )
    models = [
        ContextPacketStub(
            project_id=PROJECT_ID,
            pipeline_id=PIPELINE_ID,
            current_job_id=JOB_ID,
        ),
        ClaimNextJobRequest(project_id=PROJECT_ID, label_filter=["area:web"]),
        ClaimNextJobResponse(
            job=_job(),
            packet=packet,
            lease_seconds=900,
            lease_expires_at=LEASE_EXPIRES_AT,
            recommended_heartbeat_after_seconds=30,
        ),
        ReleaseJobResponse(job=_job()),
        ResetClaimRequest(reason="claimant crashed"),
        ResetClaimResponse(job=_job()),
        HeartbeatJobResponse(job=_job()),
    ]

    for model in models:
        _round_trip(model)

    assert models[2].lease_expires_at == datetime(2026, 4, 28, 16, 15, tzinfo=UTC)


def test_cap04_models_reject_invalid_shapes() -> None:
    with pytest.raises(ValidationError):
        ClaimNextJobRequest(project_id=PROJECT_ID, label_filter=["bad label"])
    with pytest.raises(ValidationError):
        ResetClaimRequest(reason="")
    with pytest.raises(ValidationError):
        ClaimNextJobResponse(
            job=_job(),
            packet=ContextPacketStub(
                project_id=PROJECT_ID,
                pipeline_id=PIPELINE_ID,
                current_job_id=JOB_ID,
            ),
            lease_seconds=59,
            lease_expires_at=LEASE_EXPIRES_AT,
            recommended_heartbeat_after_seconds=30,
        )
    with pytest.raises(ValidationError):
        ClaimNextJobResponse(
            job=_job(),
            packet=ContextPacketStub(
                project_id=PROJECT_ID,
                pipeline_id=PIPELINE_ID,
                current_job_id=JOB_ID,
            ),
            lease_seconds=900,
            lease_expires_at=datetime(2026, 4, 28, 16, 15),
            recommended_heartbeat_after_seconds=30,
        )


def test_cap04_settings_validate_claim_bounds() -> None:
    assert _settings().claim_lease_seconds == 900
    assert _settings().claim_sweep_interval_seconds == 60
    assert _settings(AQ_CLAIM_LEASE_SECONDS=60).claim_lease_seconds == 60
    assert _settings(AQ_CLAIM_LEASE_SECONDS=86400).claim_lease_seconds == 86400
    assert (
        _settings(AQ_CLAIM_SWEEP_INTERVAL_SECONDS=5).claim_sweep_interval_seconds
        == 5
    )
    assert (
        _settings(AQ_CLAIM_SWEEP_INTERVAL_SECONDS=3600).claim_sweep_interval_seconds
        == 3600
    )

    for value in (59, 86401):
        with pytest.raises(ValidationError):
            _settings(AQ_CLAIM_LEASE_SECONDS=value)

    for value in (4, 3601):
        with pytest.raises(ValidationError):
            _settings(AQ_CLAIM_SWEEP_INTERVAL_SECONDS=value)


def test_system_actor_seed_handles_missing_active_and_deactivated_cases(
    conninfo: str,
    seed_schema: str,
) -> None:
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _run_seed(connection, seed_schema)
        assert _active_sweeper_count(connection, seed_schema) == 1
        assert _total_sweeper_count(connection, seed_schema) == 1

        _run_seed(connection, seed_schema)
        assert _active_sweeper_count(connection, seed_schema) == 1
        assert _total_sweeper_count(connection, seed_schema) == 1

        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    UPDATE {}.actors
                    SET deactivated_at = now()
                    WHERE name = 'aq-system-sweeper'
                      AND deactivated_at IS NULL
                    """
                ).format(sql.Identifier(seed_schema))
            )

        _run_seed(connection, seed_schema)
        assert _active_sweeper_count(connection, seed_schema) == 1
        assert _total_sweeper_count(connection, seed_schema) == 2


def test_system_actor_seed_race_leaves_one_active_actor(
    conninfo: str,
    seed_schema: str,
) -> None:
    def seed_once() -> str:
        try:
            with psycopg.connect(conninfo, autocommit=True) as connection:
                _run_seed(connection, seed_schema)
        except UniqueViolation:
            return "lost_race"
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: seed_once(), range(2)))

    assert set(results) <= {"ok", "lost_race"}
    with psycopg.connect(conninfo, autocommit=True) as connection:
        assert _active_sweeper_count(connection, seed_schema) == 1
        assert _total_sweeper_count(connection, seed_schema) == 1
