import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    audit_rows,
    insert_actor_with_key,
    truncate_job_state,
)
from aq_api._audit import BusinessRuleException, audited_op
from aq_api._db import SessionLocal, engine
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from psycopg import Connection
from sqlalchemy import text

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live audited_op tests",
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


def _project_exists(conn: Connection[tuple[object, ...]], project_id: UUID) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM projects WHERE id = %s)",
            (project_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert isinstance(row[0], bool)
    return row[0]


@pytest.mark.asyncio
async def test_audited_op_success_writes_audit_and_commits(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-audit-success")
    project_id = uuid4()
    token = set_authenticated_actor_id(actor_id)

    try:
        async with SessionLocal() as session:
            async with audited_op(
                session,
                op="audit_test_success",
                target_kind="project",
                target_id=project_id,
                request_payload={"path": "success"},
            ) as audit:
                await session.execute(
                    text(
                        """
                        INSERT INTO projects
                            (id, name, slug, created_by_actor_id)
                        VALUES (:id, :name, :slug, :actor_id)
                        """
                    ),
                    {
                        "id": project_id,
                        "name": "Audit success",
                        "slug": "job-test-audit-success",
                        "actor_id": actor_id,
                    },
                )
                audit.response_payload = {"project_id": str(project_id)}
    finally:
        reset_authenticated_actor_id(token)

    assert _project_exists(conn, project_id) is True
    assert audit_rows(conn) == [
        {
            "op": "audit_test_success",
            "target_kind": "project",
            "target_id": str(project_id),
            "request_payload": {"path": "success"},
            "response_payload": {"project_id": str(project_id)},
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
async def test_audited_op_skip_success_audit_commits_without_audit(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-audit-skip")
    project_id = uuid4()
    token = set_authenticated_actor_id(actor_id)

    try:
        async with SessionLocal() as session:
            async with audited_op(
                session,
                op="audit_test_skip",
                target_kind="project",
                target_id=project_id,
                request_payload={"path": "skip"},
                skip_success_audit=True,
            ):
                await session.execute(
                    text(
                        """
                        INSERT INTO projects
                            (id, name, slug, created_by_actor_id)
                        VALUES (:id, :name, :slug, :actor_id)
                        """
                    ),
                    {
                        "id": project_id,
                        "name": "Audit skip",
                        "slug": "job-test-audit-skip",
                        "actor_id": actor_id,
                    },
                )
    finally:
        reset_authenticated_actor_id(token)

    assert _project_exists(conn, project_id) is True
    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_audited_op_business_rule_rolls_back_and_audits_denial(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-audit-denial")
    project_id = uuid4()
    token = set_authenticated_actor_id(actor_id)

    try:
        with pytest.raises(BusinessRuleException):
            async with SessionLocal() as session:
                async with audited_op(
                    session,
                    op="audit_test_denial",
                    target_kind="project",
                    target_id=project_id,
                    request_payload={"path": "denial"},
                ):
                    await session.execute(
                        text(
                            """
                            INSERT INTO projects
                                (id, name, slug, created_by_actor_id)
                            VALUES (:id, :name, :slug, :actor_id)
                            """
                        ),
                        {
                            "id": project_id,
                            "name": "Audit denial",
                            "slug": "job-test-audit-denial",
                            "actor_id": actor_id,
                        },
                    )
                    raise BusinessRuleException(
                        status_code=409,
                        error_code="audit_test_denied",
                        message="denied by test",
                    )
    finally:
        reset_authenticated_actor_id(token)

    assert _project_exists(conn, project_id) is False
    assert audit_rows(conn) == [
        {
            "op": "audit_test_denial",
            "target_kind": "project",
            "target_id": str(project_id),
            "request_payload": {"path": "denial"},
            "response_payload": {"error": "audit_test_denied"},
            "error_code": "audit_test_denied",
        }
    ]


@pytest.mark.asyncio
async def test_audited_op_unexpected_exception_rolls_back_without_audit(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-audit-exception")
    project_id = uuid4()
    token = set_authenticated_actor_id(actor_id)

    try:
        with pytest.raises(RuntimeError, match="boom"):
            async with SessionLocal() as session:
                async with audited_op(
                    session,
                    op="audit_test_exception",
                    target_kind="project",
                    target_id=project_id,
                    request_payload={"path": "exception"},
                ):
                    await session.execute(
                        text(
                            """
                            INSERT INTO projects
                                (id, name, slug, created_by_actor_id)
                            VALUES (:id, :name, :slug, :actor_id)
                            """
                        ),
                        {
                            "id": project_id,
                            "name": "Audit exception",
                            "slug": "job-test-audit-exception",
                            "actor_id": actor_id,
                        },
                    )
                    raise RuntimeError("boom")
    finally:
        reset_authenticated_actor_id(token)

    assert _project_exists(conn, project_id) is False
    assert audit_rows(conn) == []
