import asyncio
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
    AttachLabelResponse,
    DetachLabelResponse,
    RegisterLabelResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "label-test-"
PROJECT_SLUG_PREFIX = "label-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live label tests",
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
        project_slug_prefix=PROJECT_SLUG_PREFIX,
    )


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_label_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, %s)
            RETURNING id
            """,
            (actor_name, kind),
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
                f"label-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _insert_project_pipeline_job(
    conn: Connection[tuple[object, ...]],
    actor_id: UUID,
    *,
    project_slug: str,
    job_title: str,
) -> tuple[UUID, UUID]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_slug.replace("-", " ").title(), project_slug, actor_id),
        )
        project_row = cursor.fetchone()
        assert project_row is not None
        project_id = project_row[0]
        assert isinstance(project_id, UUID)

        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_id, f"{project_slug}-pipeline", actor_id),
        )
        pipeline_row = cursor.fetchone()
        assert pipeline_row is not None
        pipeline_id = pipeline_row[0]
        assert isinstance(pipeline_id, UUID)

        cursor.execute(
            """
            INSERT INTO jobs
                (
                    pipeline_id,
                    project_id,
                    state,
                    title,
                    contract,
                    created_by_actor_id
                )
            VALUES (
                %s,
                %s,
                'ready',
                %s,
                '{"contract_type":"coding-task","dod_items":[{"id":"test"}]}'::jsonb,
                %s
            )
            RETURNING id
            """,
            (pipeline_id, project_id, job_title, actor_id),
        )
        job_row = cursor.fetchone()
        assert job_row is not None
        job_id = job_row[0]
        assert isinstance(job_id, UUID)

    return project_id, job_id


def _job_labels(conn: Connection[tuple[object, ...]], job_id: UUID) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT labels FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
    assert row is not None
    labels = row[0]
    assert isinstance(labels, list)
    return [str(label) for label in labels]


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


async def _register_label(
    async_client: httpx.AsyncClient,
    key: str,
    project_id: UUID,
    name: str,
) -> RegisterLabelResponse:
    response = await async_client.post(
        f"/projects/{project_id}/labels",
        headers=_auth_headers(key),
        json={"name": name},
    )
    assert response.status_code == 200
    return RegisterLabelResponse.model_validate(response.json())


@pytest.mark.asyncio
async def test_label_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'
    project_id = "11111111-1111-4111-8111-111111111111"
    job_id = "22222222-2222-4222-8222-222222222222"

    responses = [
        await async_client.post(
            f"/projects/{project_id}/labels",
            json={"name": "area:web"},
        ),
        await async_client.post(
            f"/jobs/{job_id}/labels",
            json={"label_name": "area:web"},
        ),
        await async_client.delete(f"/jobs/{job_id}/labels/area:web"),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.content == expected


@pytest.mark.asyncio
async def test_register_attach_detach_label_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="label-test-founder")
    project_id, job_id = _insert_project_pipeline_job(
        conn,
        actor_id,
        project_slug="label-test-project",
        job_title="Ship web label",
    )
    headers = _auth_headers(key)

    register_response = await async_client.post(
        f"/projects/{project_id}/labels",
        headers=headers,
        json={"name": "area:web", "color": "#336699"},
    )
    assert register_response.status_code == 200
    registered = RegisterLabelResponse.model_validate(register_response.json())
    assert registered.label.project_id == project_id
    assert registered.label.name == "area:web"
    assert registered.label.color == "#336699"

    attach_response = await async_client.post(
        f"/jobs/{job_id}/labels",
        headers=headers,
        json={"label_name": "area:web"},
    )
    assert attach_response.status_code == 200
    attached = AttachLabelResponse.model_validate(attach_response.json())
    assert attached.job_id == job_id
    assert attached.labels == ["area:web"]

    second_attach_response = await async_client.post(
        f"/jobs/{job_id}/labels",
        headers=headers,
        json={"label_name": "area:web"},
    )
    assert second_attach_response.status_code == 200
    attached_again = AttachLabelResponse.model_validate(second_attach_response.json())
    assert attached_again.labels == ["area:web"]
    assert _job_labels(conn, job_id) == ["area:web"]

    detach_response = await async_client.delete(
        f"/jobs/{job_id}/labels/area:web",
        headers=headers,
    )
    assert detach_response.status_code == 200
    detached = DetachLabelResponse.model_validate(detach_response.json())
    assert detached.job_id == job_id
    assert detached.labels == []
    assert _job_labels(conn, job_id) == []

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == [
        "register_label",
        "attach_label",
        "attach_label",
        "detach_label",
    ]
    assert audit_rows[0]["target_kind"] == "label"
    assert audit_rows[0]["target_id"] == str(registered.label.id)
    for row in audit_rows[1:]:
        assert row["target_kind"] == "job"
        assert row["target_id"] == str(job_id)
        assert row["error_code"] is None


@pytest.mark.asyncio
async def test_cross_project_attach_returns_403_and_audits_label_not_in_project(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="label-test-founder")
    source_project_id, _source_job_id = _insert_project_pipeline_job(
        conn,
        actor_id,
        project_slug="label-test-source-project",
        job_title="Source job",
    )
    _target_project_id, target_job_id = _insert_project_pipeline_job(
        conn,
        actor_id,
        project_slug="label-test-target-project",
        job_title="Target job",
    )
    await _register_label(async_client, key, source_project_id, "area:web")

    response = await async_client.post(
        f"/jobs/{target_job_id}/labels",
        headers=_auth_headers(key),
        json={"label_name": "area:web"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "label_not_in_project"}
    assert _job_labels(conn, target_job_id) == []
    audit = _audit_rows(conn)[-1]
    assert audit["op"] == "attach_label"
    assert audit["target_kind"] == "job"
    assert audit["target_id"] == str(target_job_id)
    assert audit["error_code"] == "label_not_in_project"


@pytest.mark.asyncio
async def test_concurrent_attach_preserves_both_labels(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="label-test-founder")
    project_id, job_id = _insert_project_pipeline_job(
        conn,
        actor_id,
        project_slug="label-test-concurrency-project",
        job_title="Concurrent labels",
    )
    await _register_label(async_client, key, project_id, "area:web")
    await _register_label(async_client, key, project_id, "prio:high")

    first, second = await asyncio.gather(
        async_client.post(
            f"/jobs/{job_id}/labels",
            headers=_auth_headers(key),
            json={"label_name": "area:web"},
        ),
        async_client.post(
            f"/jobs/{job_id}/labels",
            headers=_auth_headers(key),
            json={"label_name": "prio:high"},
        ),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert set(_job_labels(conn, job_id)) == {"area:web", "prio:high"}
