import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import audited_op
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.models import JobState
from aq_api.models.db import Actor as DbActor
from aq_api.models.db import Job as DbJob

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_NAME = "aq-system-sweeper"
SYSTEM_ACTOR_KIND = "script"
CLAIM_AUTO_RELEASE_OP = "claim_auto_release"
LEASE_EXPIRED_ERROR = "lease_expired"
JOB_TARGET_KIND = "job"
READY_STATE: JobState = "ready"
IN_PROGRESS_STATE: JobState = "in_progress"
SWEEP_BATCH_SIZE = 100

SleepCallable = Callable[[int], Awaitable[None]]
NowFactory = Callable[[], datetime]


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


async def _active_system_actor_id(session: AsyncSession) -> UUID | None:
    statement = select(DbActor.id).where(
        DbActor.name == SYSTEM_ACTOR_NAME,
        DbActor.deactivated_at.is_(None),
    )
    return cast(UUID | None, await session.scalar(statement))


async def ensure_system_actor(session: AsyncSession) -> UUID:
    actor_id = await _active_system_actor_id(session)
    if actor_id is not None:
        return actor_id

    actor = DbActor(name=SYSTEM_ACTOR_NAME, kind=SYSTEM_ACTOR_KIND)
    session.add(actor)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        actor_id = await _active_system_actor_id(session)
        if actor_id is None:
            raise
        return actor_id

    return actor.id


async def run_claim_auto_release_once(
    session: AsyncSession,
    *,
    now: datetime,
    system_actor_id: UUID | None = None,
) -> int:
    from aq_api._settings import settings

    lease_seconds = settings.claim_lease_seconds
    if system_actor_id is None:
        system_actor_id = await ensure_system_actor(session)
        await session.commit()

    stale_before = now.astimezone(UTC) - timedelta(seconds=lease_seconds)
    token = set_authenticated_actor_id(system_actor_id)
    try:
        statement = (
            select(DbJob)
            .where(
                DbJob.state == IN_PROGRESS_STATE,
                DbJob.claim_heartbeat_at < stale_before,
            )
            .order_by(DbJob.claim_heartbeat_at.asc(), DbJob.id.asc())
            .limit(SWEEP_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        stale_jobs = list((await session.scalars(statement)).all())
        released = 0

        for db_job in stale_jobs:
            previous_claimant_actor_id = db_job.claimed_by_actor_id
            stale_claim_heartbeat_at = db_job.claim_heartbeat_at
            if stale_claim_heartbeat_at is None:
                continue

            previous_claimant_text = (
                str(previous_claimant_actor_id)
                if previous_claimant_actor_id is not None
                else None
            )
            request_payload = {
                "previous_claimant_actor_id": previous_claimant_text,
                "stale_claim_heartbeat_at": _utc_text(stale_claim_heartbeat_at),
                "lease_seconds": lease_seconds,
                "reason": LEASE_EXPIRED_ERROR,
            }
            async with audited_op(
                session,
                op=CLAIM_AUTO_RELEASE_OP,
                target_kind=JOB_TARGET_KIND,
                target_id=db_job.id,
                request_payload=request_payload,
            ) as audit:
                db_job.state = READY_STATE
                db_job.claimed_by_actor_id = None
                db_job.claimed_at = None
                db_job.claim_heartbeat_at = None
                await session.flush()
                audit.error_code = LEASE_EXPIRED_ERROR
                audit.response_payload = {
                    "released": True,
                    "previous_claimant_actor_id": previous_claimant_text,
                    "stale_claim_heartbeat_at": _utc_text(stale_claim_heartbeat_at),
                    "lease_seconds": lease_seconds,
                    "reason": LEASE_EXPIRED_ERROR,
                }
                released += 1

        return released
    finally:
        reset_authenticated_actor_id(token)


async def claim_auto_release_loop(
    initial_system_actor_id: UUID | None,
    *,
    sleep: SleepCallable = asyncio.sleep,
    now_factory: NowFactory = lambda: datetime.now(UTC),
) -> None:
    from aq_api._db import SessionLocal
    from aq_api._settings import settings

    system_actor_id = initial_system_actor_id
    while True:
        try:
            await sleep(settings.claim_sweep_interval_seconds)
            async with SessionLocal() as session:
                system_actor_id = await ensure_system_actor(session)
                await session.commit()
                await run_claim_auto_release_once(
                    session,
                    now=now_factory(),
                    system_actor_id=system_actor_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("claim auto-release sweep failed; will retry: %s", exc)
