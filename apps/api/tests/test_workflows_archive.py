import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_cap03_state
from aq_api.app import app
from aq_api.models import (
    ArchiveWorkflowResponse,
    CreateWorkflowResponse,
    GetWorkflowResponse,
    ListWorkflowsResponse,
    UpdateWorkflowResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "workflow-archive-test-"
WORKFLOW_SLUG_PREFIX = "workflow-archive-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live workflow tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_cap03_state(connection)
        yield connection
        _truncate_cap03_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    from aq_api._db import engine

    await engine.dispose()


def _truncate_cap03_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_cap03_state(
        conn,
        actor_name_prefix=ACTOR_PREFIX,
        project_slug_prefix=WORKFLOW_SLUG_PREFIX,
    )


def _insert_actor_with_key(conn: Connection[tuple[object, ...]]) -> str:
    api_key = f"aq2_workflow_archive_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (f"{ACTOR_PREFIX}founder",),
        )
        actor_row = cursor.fetchone()
        assert actor_row is not None
        actor_id = actor_row[0]
        assert isinstance(actor_id, UUID)

        cursor.execute(
            """
            INSERT INTO api_keys
                (actor_id, name, key_hash, prefix, lookup_id)
            VALUES
                (%s, %s, %s, %s, %s)
            """,
            (
                actor_id,
                f"workflow-archive-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _contract_profile_id(conn: Connection[tuple[object, ...]]) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM contract_profiles WHERE name = 'coding-task' LIMIT 1"
        )
        row = cursor.fetchone()
    assert row is not None
    profile_id = row[0]
    assert isinstance(profile_id, UUID)
    return str(profile_id)


def _steps(profile_id: str, *names: str) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "ordinal": ordinal,
            "default_contract_profile_id": profile_id,
            "step_edges": {},
        }
        for ordinal, name in enumerate(names, start=1)
    ]


def _workflow_archive_flags(
    conn: Connection[tuple[object, ...]],
    slug: str,
) -> list[tuple[int, bool]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, is_archived
            FROM workflows
            WHERE slug = %s
            ORDER BY version ASC
            """,
            (slug,),
        )
        rows = cursor.fetchall()
    return [(int(row[0]), bool(row[1])) for row in rows]


def _audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            ORDER BY ts ASC, id ASC
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        rows = cursor.fetchall()
    return [
        {
            "op": row[0],
            "target_kind": row[1],
            "target_id": str(row[2]) if row[2] is not None else None,
            "request_payload": row[3],
            "response_payload": row[4],
            "error_code": row[5],
        }
        for row in rows
    ]


@pytest.mark.asyncio
async def test_archive_workflow_archives_all_family_versions(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    key = _insert_actor_with_key(conn)
    headers = _auth_headers(key)
    profile_id = _contract_profile_id(conn)
    slug = "workflow-archive-test-family"

    create_response = await async_client.post(
        "/workflows",
        headers=headers,
        json={
            "name": "Archive Family",
            "slug": slug,
            "steps": _steps(profile_id, "scope", "build", "verify"),
        },
    )
    assert create_response.status_code == 200
    created = CreateWorkflowResponse.model_validate(create_response.json())

    update_response = await async_client.patch(
        f"/workflows/{created.workflow.id}",
        headers=headers,
        json={
            "name": "Archive Family v2",
            "steps": _steps(profile_id, "scope", "design", "build", "verify"),
        },
    )
    assert update_response.status_code == 200
    updated = UpdateWorkflowResponse.model_validate(update_response.json())
    assert _workflow_archive_flags(conn, slug) == [(1, False), (2, False)]

    archive_response = await async_client.post(
        f"/workflows/{slug}/archive",
        headers=headers,
        json={},
    )

    assert archive_response.status_code == 200
    archived = ArchiveWorkflowResponse.model_validate(archive_response.json())
    assert archived.slug == slug
    assert archived.archived_count == 2
    assert _workflow_archive_flags(conn, slug) == [(1, True), (2, True)]

    for workflow_id in (created.workflow.id, updated.workflow.id):
        get_response = await async_client.get(
            f"/workflows/{workflow_id}",
            headers=headers,
        )
        assert get_response.status_code == 200
        workflow = GetWorkflowResponse.model_validate(get_response.json()).workflow
        assert workflow.is_archived is True

    default_list = await async_client.get(
        "/workflows",
        headers=headers,
        params={"limit": 200},
    )
    assert default_list.status_code == 200
    default_page = ListWorkflowsResponse.model_validate(default_list.json())
    assert {created.workflow.id, updated.workflow.id}.isdisjoint(
        {workflow.id for workflow in default_page.workflows}
    )

    archived_list = await async_client.get(
        "/workflows",
        headers=headers,
        params={"include_archived": "true", "limit": 200},
    )
    assert archived_list.status_code == 200
    archived_page = ListWorkflowsResponse.model_validate(archived_list.json())
    assert {created.workflow.id, updated.workflow.id}.issubset(
        {workflow.id for workflow in archived_page.workflows}
    )

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "create_workflow",
        "update_workflow",
        "archive_workflow",
    ]
    archive_audit = audit_rows[-1]
    assert archive_audit["target_kind"] == "workflow"
    assert archive_audit["target_id"] == str(updated.workflow.id)
    assert archive_audit["request_payload"] == {"slug": slug}
    assert archive_audit["response_payload"] == {
        "slug": slug,
        "archived_count": 2,
    }
    assert archive_audit["error_code"] is None
