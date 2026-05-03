import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from _isolated_schema import (
    connect_in_schema,
    create_async_engine_in_schema,
    create_cap04_schema,
    drop_schema,
    sync_conninfo,
)
from _jobs_test_support import (
    audit_rows,
    insert_actor_with_key,
    truncate_job_state,
)
from aq_api._audit import BusinessRuleException, audited_op
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from psycopg import Connection
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC or not DATABASE_URL,
    reason="DATABASE_URL and DATABASE_URL_SYNC are required for live audit tests",
)


def _session_local() -> object:
    from aq_api._db import SessionLocal

    return SessionLocal


@pytest.fixture()
def isolated_schema() -> Iterator[str]:
    assert DATABASE_URL_SYNC is not None
    conninfo = sync_conninfo(DATABASE_URL_SYNC)
    schema = create_cap04_schema(conninfo, prefix="cap05_bre")
    try:
        yield schema
    finally:
        drop_schema(conninfo, schema)


@pytest.fixture(autouse=True)
async def isolate_async_session_local(
    monkeypatch: pytest.MonkeyPatch,
    isolated_schema: str,
) -> AsyncIterator[None]:
    assert DATABASE_URL is not None
    import aq_api._db as db_module

    isolated_engine = create_async_engine_in_schema(DATABASE_URL, isolated_schema)
    isolated_session_local = async_sessionmaker(isolated_engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(db_module, "SessionLocal", isolated_session_local)
    try:
        yield
    finally:
        await isolated_engine.dispose()


@pytest.fixture()
def conn(isolated_schema: str) -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = sync_conninfo(DATABASE_URL_SYNC)
    with connect_in_schema(conninfo, isolated_schema) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


def test_business_rule_response_omits_details_when_unset() -> None:
    from aq_api.routes._errors import business_rule_response

    exc = BusinessRuleException(
        status_code=409,
        error_code="job_not_in_progress",
        message="wrong state",
    )

    response = business_rule_response(exc)

    assert response.status_code == 409
    assert response.body == b'{"error":"job_not_in_progress"}'


def test_business_rule_response_includes_details_when_present() -> None:
    from aq_api.routes._errors import business_rule_response

    exc = BusinessRuleException(
        status_code=422,
        error_code="contract_violation",
        message="contract mismatch",
        details={"rule": "dod_id_unknown", "dod_id": "missing"},
    )

    response = business_rule_response(exc)

    assert response.status_code == 422
    assert response.body == (
        b'{"error":"contract_violation","details":{"rule":"dod_id_unknown",'
        b'"dod_id":"missing"}}'
    )


@pytest.mark.asyncio
async def test_audited_op_business_rule_details_are_recorded_on_denial(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = insert_actor_with_key(conn, name="job-test-bre-details")
    project_id = uuid4()
    token = set_authenticated_actor_id(actor_id)

    try:
        with pytest.raises(BusinessRuleException):
            async with _session_local()() as session:
                async with audited_op(
                    session,
                    op="audit_test_details",
                    target_kind="project",
                    target_id=project_id,
                    request_payload={"path": "details"},
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
                            "name": "Audit details",
                            "slug": "job-test-bre-details",
                            "actor_id": actor_id,
                        },
                    )
                    raise BusinessRuleException(
                        status_code=422,
                        error_code="contract_violation",
                        message="contract mismatch",
                        details={"rule": "dod_id_unknown", "dod_id": "missing"},
                    )
    finally:
        reset_authenticated_actor_id(token)

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM projects WHERE id = %s)",
            (project_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] is False
    assert audit_rows(conn) == [
        {
            "op": "audit_test_details",
            "target_kind": "project",
            "target_id": str(project_id),
            "request_payload": {"path": "details"},
            "response_payload": {
                "error": "contract_violation",
                "details": {"rule": "dod_id_unknown", "dod_id": "missing"},
            },
            "error_code": "contract_violation",
        }
    ]


def test_business_rule_exception_keeps_details_attribute() -> None:
    details = {"rule": "missing_required_dod", "dod_id": "tests-pass"}

    exc = BusinessRuleException(
        status_code=422,
        error_code="contract_violation",
        message="contract mismatch",
        details=details,
    )

    assert exc.status_code == 422
    assert exc.error_code == "contract_violation"
    assert exc.details == details
    assert str(exc) == "contract mismatch"
