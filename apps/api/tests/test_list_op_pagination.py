from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from _dl_test_support import insert_decision, insert_learning
from _jobs_test_support import auth_headers
from _submit_job_test_support import DB_SKIP, fixture_project
from _submit_job_test_support import (
    async_client as async_client,  # noqa: F401
)
from _submit_job_test_support import (
    conn as conn,  # noqa: F401
)
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import (
    isolated_schema as isolated_schema,  # noqa: F401
)
from psycopg import Connection

pytestmark = DB_SKIP


async def _assert_cursor_pagination(
    async_client: httpx.AsyncClient,
    *,
    path: str,
    key: str,
    expected_ids_desc: list[UUID],
) -> None:
    first_page = await async_client.get(
        path,
        headers=auth_headers(key),
        params={"limit": 2},
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert [item["id"] for item in first_payload["items"]] == [
        str(expected_ids_desc[0]),
        str(expected_ids_desc[1]),
    ]
    assert first_payload["next_cursor"] is not None

    second_page = await async_client.get(
        path,
        headers=auth_headers(key),
        params={"limit": 2, "cursor": first_payload["next_cursor"]},
    )
    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert [item["id"] for item in second_payload["items"]] == [
        str(expected_ids_desc[2])
    ]
    assert second_payload["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_decisions_cursor_order_clamp_and_deactivated_filter(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    older = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="older",
        created_at_offset_seconds=-30,
    )
    middle = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="middle",
        created_at_offset_seconds=-20,
    )
    newer = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="newer",
        created_at_offset_seconds=-10,
    )
    inactive = insert_decision(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="inactive",
        created_at_offset_seconds=-5,
        deactivated=True,
    )

    await _assert_cursor_pagination(
        async_client,
        path="/decisions",
        key=key,
        expected_ids_desc=[newer, middle, older],
    )

    clamped = await async_client.get(
        "/decisions",
        headers=auth_headers(key),
        params={"limit": 500},
    )
    assert clamped.status_code == 200
    assert len(clamped.json()["items"]) == 3

    with_inactive = await async_client.get(
        "/decisions",
        headers=auth_headers(key),
        params={"include_deactivated": True, "limit": 10},
    )
    assert with_inactive.status_code == 200
    assert [item["id"] for item in with_inactive.json()["items"]] == [
        str(inactive),
        str(newer),
        str(middle),
        str(older),
    ]


@pytest.mark.asyncio
async def test_list_learnings_cursor_order_clamp_and_deactivated_filter(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, _pipeline_id = fixture_project(conn)
    older = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="older",
        created_at_offset_seconds=-30,
    )
    middle = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="middle",
        created_at_offset_seconds=-20,
    )
    newer = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="newer",
        created_at_offset_seconds=-10,
    )
    inactive = insert_learning(
        conn,
        attached_to_kind="project",
        attached_to_id=project_id,
        created_by_actor_id=actor_id,
        title="inactive",
        created_at_offset_seconds=-5,
        deactivated=True,
    )

    await _assert_cursor_pagination(
        async_client,
        path="/learnings",
        key=key,
        expected_ids_desc=[newer, middle, older],
    )

    clamped = await async_client.get(
        "/learnings",
        headers=auth_headers(key),
        params={"limit": 500},
    )
    assert clamped.status_code == 200
    assert len(clamped.json()["items"]) == 3

    with_inactive = await async_client.get(
        "/learnings",
        headers=auth_headers(key),
        params={"include_deactivated": True, "limit": 10},
    )
    assert with_inactive.status_code == 200
    assert [item["id"] for item in with_inactive.json()["items"]] == [
        str(inactive),
        str(newer),
        str(middle),
        str(older),
    ]
