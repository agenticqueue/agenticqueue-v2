import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from aq_api._audit import BusinessRuleException, audited_op
from aq_api._request_context import (
    reset_authenticated_actor_id,
    reset_claimed_actor_identity,
    set_authenticated_actor_id,
    set_claimed_actor_identity,
)
from aq_api.models.db import Actor, AuditLogEntry
from aq_api.services.audit import redact_secrets
from psycopg import Connection
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL_SYNC,
    reason="DATABASE_URL and DATABASE_URL_SYNC are required for live audit tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _cleanup(connection)
        yield connection
        _cleanup(connection)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    from aq_api._db import SessionLocal, engine

    async with SessionLocal() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


def _cleanup(conn: Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE op LIKE 'audit-test-%'
               OR claimed_actor_identity LIKE 'audit-test-%'
            """
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE 'audit-test-%'")


def _insert_actor(conn: Connection[tuple[object, ...]], name: str) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (name,),
        )
        row = cursor.fetchone()
    assert row is not None
    actor_id = row[0]
    assert isinstance(actor_id, UUID)
    return actor_id


async def _count_actor(session: AsyncSession, name: str) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Actor).where(Actor.name == name)
        )
    )


async def _count_audit(session: AsyncSession, op: str) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(AuditLogEntry.op == op)
        )
    )


async def _audit_row(session: AsyncSession, op: str) -> AuditLogEntry:
    row = await session.scalar(select(AuditLogEntry).where(AuditLogEntry.op == op))
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_success_commits_domain_and_audit_rows(
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    auth_actor_id = _insert_actor(conn, f"audit-test-auth-{uuid.uuid4()}")
    domain_name = f"audit-test-domain-{uuid.uuid4()}"
    op = f"audit-test-success-{uuid.uuid4()}"
    actor_token = set_authenticated_actor_id(auth_actor_id)
    identity_token = set_claimed_actor_identity("audit-test-agent-success")
    try:
        async with audited_op(
            session,
            op=op,
            target_kind="actor",
            request_payload={"name": domain_name},
        ) as audit:
            domain_actor = Actor(name=domain_name, kind="agent")
            session.add(domain_actor)
            await session.flush()
            audit.target_id = domain_actor.id
            audit.response_payload = {"id": str(domain_actor.id)}
    finally:
        reset_claimed_actor_identity(identity_token)
        reset_authenticated_actor_id(actor_token)

    assert await _count_actor(session, domain_name) == 1
    audit = await _audit_row(session, op)
    assert audit.authenticated_actor_id == auth_actor_id
    assert audit.claimed_actor_identity == "audit-test-agent-success"
    assert audit.target_kind == "actor"
    assert audit.target_id is not None
    assert audit.request_payload == {"name": domain_name}
    assert audit.response_payload == {"id": str(audit.target_id)}
    assert audit.error_code is None


@pytest.mark.asyncio
async def test_audit_write_failure_rolls_back_domain_row(
    monkeypatch: pytest.MonkeyPatch,
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    auth_actor_id = _insert_actor(conn, f"audit-test-auth-{uuid.uuid4()}")
    domain_name = f"audit-test-domain-{uuid.uuid4()}"
    op = f"audit-test-rollback-{uuid.uuid4()}"

    async def fail_record(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr("aq_api._audit.record", fail_record)
    actor_token = set_authenticated_actor_id(auth_actor_id)
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            async with audited_op(session, op=op, target_kind="actor"):
                session.add(Actor(name=domain_name, kind="agent"))
                await session.flush()
    finally:
        reset_authenticated_actor_id(actor_token)

    assert await _count_actor(session, domain_name) == 0
    assert await _count_audit(session, op) == 0


@pytest.mark.asyncio
async def test_business_rule_denial_commits_audit_only_row(
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    auth_actor_id = _insert_actor(conn, f"audit-test-auth-{uuid.uuid4()}")
    domain_name = f"audit-test-domain-{uuid.uuid4()}"
    op = f"audit-test-denial-{uuid.uuid4()}"
    actor_token = set_authenticated_actor_id(auth_actor_id)
    try:
        with pytest.raises(BusinessRuleException):
            async with audited_op(
                session,
                op=op,
                target_kind="actor",
                request_payload={"name": domain_name},
            ):
                session.add(Actor(name=domain_name, kind="agent"))
                await session.flush()
                raise BusinessRuleException(
                    status_code=403,
                    error_code="actor_forbidden",
                    message="actor forbidden",
                )
    finally:
        reset_authenticated_actor_id(actor_token)

    assert await _count_actor(session, domain_name) == 0
    audit = await _audit_row(session, op)
    assert audit.authenticated_actor_id == auth_actor_id
    assert audit.request_payload == {"name": domain_name}
    assert audit.response_payload == {"error": "actor_forbidden"}
    assert audit.error_code == "actor_forbidden"


@pytest.mark.asyncio
async def test_unexpected_exception_rolls_back_without_audit(
    conn: Connection[tuple[object, ...]],
    session: AsyncSession,
) -> None:
    auth_actor_id = _insert_actor(conn, f"audit-test-auth-{uuid.uuid4()}")
    domain_name = f"audit-test-domain-{uuid.uuid4()}"
    op = f"audit-test-unexpected-{uuid.uuid4()}"
    actor_token = set_authenticated_actor_id(auth_actor_id)
    try:
        with pytest.raises(ValueError, match="boom"):
            async with audited_op(session, op=op, target_kind="actor"):
                session.add(Actor(name=domain_name, kind="agent"))
                await session.flush()
                raise ValueError("boom")
    finally:
        reset_authenticated_actor_id(actor_token)

    assert await _count_actor(session, domain_name) == 0
    assert await _count_audit(session, op) == 0


def test_recursive_redactor_strips_secret_fields_at_any_depth() -> None:
    payload = {
        "safe": "kept",
        "api_key": "secret-1",
        "nested": {
            "token": "secret-2",
            "items": [{"db_password_hash": "secret-3"}, {"name": "kept"}],
        },
    }

    assert redact_secrets(payload) == {
        "safe": "kept",
        "api_key": "[REDACTED]",
        "nested": {
            "token": "[REDACTED]",
            "items": [{"db_password_hash": "[REDACTED]"}, {"name": "kept"}],
        },
    }


@pytest.mark.asyncio
async def test_claimed_actor_identity_is_task_scoped_under_concurrency(
    conn: Connection[tuple[object, ...]],
) -> None:
    auth_actor_id = _insert_actor(conn, f"audit-test-auth-{uuid.uuid4()}")
    ops = [
        f"audit-test-context-a-{uuid.uuid4()}",
        f"audit-test-context-b-{uuid.uuid4()}",
    ]
    identities = ["audit-test-agent-a", "audit-test-agent-b"]

    async def write_audit(op: str, identity: str) -> None:
        from aq_api._db import SessionLocal

        actor_token = set_authenticated_actor_id(auth_actor_id)
        identity_token = set_claimed_actor_identity(identity)
        try:
            async with SessionLocal() as local_session:
                async with audited_op(local_session, op=op, target_kind="actor"):
                    pass
        finally:
            reset_claimed_actor_identity(identity_token)
            reset_authenticated_actor_id(actor_token)

    await asyncio.gather(
        write_audit(ops[0], identities[0]),
        write_audit(ops[1], identities[1]),
    )

    from aq_api._db import SessionLocal, engine

    async with SessionLocal() as check_session:
        rows = (
            await check_session.execute(
                select(AuditLogEntry.op, AuditLogEntry.claimed_actor_identity).where(
                    AuditLogEntry.op.in_(ops)
                )
            )
        ).all()
    await engine.dispose()

    assert dict(rows) == dict(zip(ops, identities, strict=True))
