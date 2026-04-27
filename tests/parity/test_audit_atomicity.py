from __future__ import annotations

import os
from collections.abc import Callable
from uuid import UUID

import pytest
from sqlalchemy import func, select

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL"),
        reason="DATABASE_URL is required for live audit atomicity tests",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]


async def _setup_founder() -> tuple[UUID, str]:
    from aq_api._db import SessionLocal
    from aq_api.services.setup import run_setup

    async with SessionLocal() as session:
        setup = await run_setup(session)
    return setup.actor_id, setup.founder_key


async def _actor_count(name: str | None = None) -> int:
    from aq_api._db import SessionLocal
    from aq_api.models.db import Actor as DbActor

    statement = select(func.count()).select_from(DbActor)
    if name is not None:
        statement = statement.where(DbActor.name == name)
    async with SessionLocal() as session:
        value = await session.scalar(statement)
    return int(value or 0)


async def _audit_count(error_code: str | None = None) -> int:
    from aq_api._db import SessionLocal
    from aq_api.models.db import AuditLogEntry

    statement = select(func.count()).select_from(AuditLogEntry)
    if error_code is not None:
        statement = statement.where(AuditLogEntry.error_code == error_code)
    async with SessionLocal() as session:
        value = await session.scalar(statement)
    return int(value or 0)


async def test_domain_and_audit_roll_back_when_audit_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
    truncate_db: Callable[[], None],
) -> None:
    from aq_api._audit import audited_op
    from aq_api._db import SessionLocal
    from aq_api._request_context import (
        reset_authenticated_actor_id,
        set_authenticated_actor_id,
    )
    from aq_api.models.db import Actor as DbActor

    truncate_db()
    founder_id, _founder_key = await _setup_founder()

    async def fail_record(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced audit insert failure")

    monkeypatch.setattr("aq_api._audit.record", fail_record)
    context_token = set_authenticated_actor_id(founder_id)
    try:
        async with SessionLocal() as session:
            with pytest.raises(RuntimeError, match="forced audit insert failure"):
                async with audited_op(
                    session,
                    op="atomicity_create_actor",
                    target_kind="actor",
                ):
                    session.add(DbActor(name="atomicity-rollback", kind="agent"))
                    await session.flush()
    finally:
        reset_authenticated_actor_id(context_token)

    assert await _actor_count("atomicity-rollback") == 0
    assert await _audit_count() == 0


async def test_business_rule_denial_rolls_back_domain_but_commits_audit(
    truncate_db: Callable[[], None],
) -> None:
    from aq_api._audit import BusinessRuleException
    from aq_api._db import SessionLocal
    from aq_api._request_context import (
        reset_authenticated_actor_id,
        set_authenticated_actor_id,
    )
    from aq_api.models import CreateActorRequest
    from aq_api.services.actors import create_actor

    truncate_db()
    founder_id, _founder_key = await _setup_founder()
    context_token = set_authenticated_actor_id(founder_id)
    try:
        async with SessionLocal() as session:
            first = await create_actor(
                session,
                CreateActorRequest(name="atomicity-denial", kind="agent"),
            )
        before_actor_count = await _actor_count()

        with pytest.raises(BusinessRuleException) as exc_info:
            async with SessionLocal() as session:
                await create_actor(
                    session,
                    CreateActorRequest(name=first.actor.name, kind="agent"),
                )
    finally:
        reset_authenticated_actor_id(context_token)

    assert exc_info.value.error_code == "actor_already_exists"
    assert await _actor_count() == before_actor_count
    assert await _audit_count() == 2
    assert await _audit_count("actor_already_exists") == 1
