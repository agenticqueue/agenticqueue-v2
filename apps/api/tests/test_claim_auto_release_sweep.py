import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    job_row,
    truncate_job_state,
)
from aq_api._datetime import parse_utc
from aq_api._db import SessionLocal, engine
from aq_api._request_context import get_authenticated_actor_id
from aq_api.services.claim_auto_release import (
    SYSTEM_ACTOR_NAME,
    ensure_system_actor,
    run_claim_auto_release_once,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live auto-release sweep tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_after_test() -> AsyncIterator[None]:
    yield
    await engine.dispose()


def _fixture_project(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, UUID, UUID]:
    actor_id, _key = insert_actor_with_key(
        conn,
        name=f"job-test-sweep-founder-{uuid4().hex[:12]}",
    )
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    return actor_id, project_id, pipeline_id


def _mark_claimed(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
    *,
    actor_id: UUID,
    heartbeat_at: datetime,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET state = 'in_progress',
                claimed_by_actor_id = %s,
                claimed_at = %s,
                claim_heartbeat_at = %s
            WHERE id = %s
            """,
            (actor_id, heartbeat_at, heartbeat_at, job_id),
        )


def _claimed_job(
    conn: Connection[tuple[object, ...]],
    *,
    heartbeat_at: datetime,
    title: str = "sweep target",
) -> tuple[UUID, UUID]:
    claimant_id, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=claimant_id,
        title=title,
    )
    _mark_claimed(conn, job_id, actor_id=claimant_id, heartbeat_at=heartbeat_at)
    return claimant_id, job_id


def _deactivate_active_system_actor(conn: Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE actors
            SET deactivated_at = now()
            WHERE name = %s AND deactivated_at IS NULL
            """,
            (SYSTEM_ACTOR_NAME,),
        )


def _active_system_actor_rows(conn: Connection[tuple[object, ...]]) -> list[UUID]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM actors
            WHERE name = %s AND deactivated_at IS NULL
            ORDER BY created_at, id
            """,
            (SYSTEM_ACTOR_NAME,),
        )
        rows = cursor.fetchall()
    return [row[0] for row in rows if isinstance(row[0], UUID)]


def _auto_release_audit_row(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT audit_log.op,
                   audit_log.target_kind,
                   audit_log.target_id,
                   audit_log.authenticated_actor_id,
                   actors.name AS actor_name,
                   audit_log.request_payload,
                   audit_log.response_payload,
                   audit_log.error_code
            FROM audit_log
            JOIN actors ON actors.id = audit_log.authenticated_actor_id
            WHERE audit_log.op = 'claim_auto_release'
              AND audit_log.target_id = %s
            ORDER BY audit_log.ts DESC, audit_log.id DESC
            LIMIT 1
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)


@pytest.mark.asyncio
async def test_run_claim_auto_release_once_releases_stale_job_and_audits(
    conn: Connection[tuple[object, ...]],
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    stale_heartbeat = now - timedelta(seconds=901)
    claimant_id, job_id = _claimed_job(conn, heartbeat_at=stale_heartbeat)

    async with SessionLocal() as session:
        released = await run_claim_auto_release_once(session, now=now)

    assert released == 1
    stored = job_row(conn, job_id)
    assert stored["state"] == "ready"
    assert stored["claimed_by_actor_id"] is None
    assert stored["claimed_at"] is None
    assert stored["claim_heartbeat_at"] is None
    assert get_authenticated_actor_id() is None

    audit = _auto_release_audit_row(conn, job_id)
    assert audit["op"] == "claim_auto_release"
    assert audit["target_kind"] == "job"
    assert audit["target_id"] == job_id
    assert audit["actor_name"] == SYSTEM_ACTOR_NAME
    assert audit["error_code"] == "lease_expired"
    request_payload = audit["request_payload"]
    assert request_payload["previous_claimant_actor_id"] == str(claimant_id)
    assert parse_utc(request_payload["stale_claim_heartbeat_at"]) == stale_heartbeat
    assert request_payload["lease_seconds"] == 900
    assert request_payload["reason"] == "lease_expired"
    assert audit["response_payload"]["released"] is True


@pytest.mark.asyncio
async def test_run_claim_auto_release_once_does_not_touch_fresh_job(
    conn: Connection[tuple[object, ...]],
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh_heartbeat = now - timedelta(seconds=450)
    claimant_id, job_id = _claimed_job(conn, heartbeat_at=fresh_heartbeat)

    async with SessionLocal() as session:
        released = await run_claim_auto_release_once(session, now=now)

    assert released == 0
    stored = job_row(conn, job_id)
    assert stored["state"] == "in_progress"
    assert stored["claimed_by_actor_id"] == claimant_id
    assert stored["claim_heartbeat_at"] == fresh_heartbeat


@pytest.mark.asyncio
async def test_run_claim_auto_release_once_releases_multiple_stale_jobs(
    conn: Connection[tuple[object, ...]],
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    job_ids = [
        _claimed_job(
            conn,
            heartbeat_at=now - timedelta(seconds=901 + index),
            title=f"sweep target {index}",
        )[1]
        for index in range(3)
    ]

    async with SessionLocal() as session:
        released = await run_claim_auto_release_once(session, now=now)

    assert released == 3
    for job_id in job_ids:
        stored = job_row(conn, job_id)
        assert stored["state"] == "ready"
        assert stored["claimed_by_actor_id"] is None
        assert _auto_release_audit_row(conn, job_id)["error_code"] == "lease_expired"


@pytest.mark.asyncio
async def test_ensure_system_actor_idempotent_missing_and_deactivated_cases(
    conn: Connection[tuple[object, ...]],
) -> None:
    _deactivate_active_system_actor(conn)

    async with SessionLocal() as session:
        first_id = await ensure_system_actor(session)
        await session.commit()
        second_id = await ensure_system_actor(session)
        await session.commit()
        third_id = await ensure_system_actor(session)
        await session.commit()

    assert first_id == second_id == third_id
    assert _active_system_actor_rows(conn) == [first_id]

    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE actors
            SET deactivated_at = now()
            WHERE id = %s
            """,
            (first_id,),
        )

    async with SessionLocal() as session:
        replacement_id = await ensure_system_actor(session)
        await session.commit()

    assert replacement_id != first_id
    assert _active_system_actor_rows(conn) == [replacement_id]


@pytest.mark.asyncio
async def test_ensure_system_actor_concurrent_calls_return_one_active_row(
    conn: Connection[tuple[object, ...]],
) -> None:
    _deactivate_active_system_actor(conn)

    async def call_ensure() -> UUID:
        async with SessionLocal() as session:
            actor_id = await ensure_system_actor(session)
            await session.commit()
            return actor_id

    left, right = await asyncio.gather(call_ensure(), call_ensure())

    assert left == right
    assert _active_system_actor_rows(conn) == [left]


@pytest.mark.asyncio
async def test_run_claim_auto_release_once_recreates_missing_system_actor(
    conn: Connection[tuple[object, ...]],
) -> None:
    _deactivate_active_system_actor(conn)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _claimant_id, job_id = _claimed_job(
        conn,
        heartbeat_at=now - timedelta(seconds=901),
    )

    async with SessionLocal() as session:
        released = await run_claim_auto_release_once(session, now=now)

    assert released == 1
    active_ids = _active_system_actor_rows(conn)
    assert len(active_ids) == 1
    assert _auto_release_audit_row(conn, job_id)["authenticated_actor_id"] == (
        active_ids[0]
    )
