from uuid import UUID

import httpx
import psycopg
import pytest
from _submit_job_test_support import (
    DB_SKIP,
    auth_headers,
    claimed_job,
    fixture_project,
    insert_job,
)
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


def _done_payload() -> dict[str, object]:
    return {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": [
                    "pytest -q apps/api/tests/test_submit_dl_per_attached_kind.py"
                ],
                "summary": "attached kind tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "no docs touched",
            },
        ],
        "commands_run": [
            "pytest -q apps/api/tests/test_submit_dl_per_attached_kind.py"
        ],
        "verification_summary": "attached kind behavior verified",
        "files_changed": ["apps/api/tests/test_submit_dl_per_attached_kind.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-80",
        "decisions_made": [
            {
                "title": "Submit attaches to job",
                "statement": "Submit-time decisions are Job-attached only.",
                "rationale": "Standalone D&L ops land in cap #9.",
            }
        ],
        "learnings": [
            {
                "title": "Submit learning attaches to job",
                "statement": "Submit-time learnings are Job-attached only.",
                "context": "Schema admits all three kinds for cap #9.",
            }
        ],
    }


def _attached_kind_counts(
    conn: Connection[tuple[object, ...]],
    *,
    table: str,
) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT attached_to_kind, count(*)
            FROM {table}
            GROUP BY attached_to_kind
            ORDER BY attached_to_kind
            """
        )
        rows = cursor.fetchall()
    return {str(kind): int(count) for kind, count in rows}


def _insert_direct_decision(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    actor_id: UUID,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO decisions
                (
                    attached_to_kind,
                    attached_to_id,
                    title,
                    statement,
                    rationale,
                    created_by_actor_id
                )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                attached_to_kind,
                attached_to_id,
                f"{attached_to_kind} decision",
                f"{attached_to_kind} decision statement",
                None,
                actor_id,
            ),
        )


def _insert_direct_learning(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    actor_id: UUID,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO learnings
                (
                    attached_to_kind,
                    attached_to_id,
                    title,
                    statement,
                    context,
                    created_by_actor_id
                )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                attached_to_kind,
                attached_to_id,
                f"{attached_to_kind} learning",
                f"{attached_to_kind} learning statement",
                None,
                actor_id,
            ),
        )


def test_decision_and_learning_schema_accept_all_three_attached_kinds(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="attached kind job",
    )
    target_ids = {
        "job": job_id,
        "pipeline": pipeline_id,
        "project": project_id,
    }

    for kind, target_id in target_ids.items():
        _insert_direct_decision(
            conn,
            attached_to_kind=kind,
            attached_to_id=target_id,
            actor_id=actor_id,
        )
        _insert_direct_learning(
            conn,
            attached_to_kind=kind,
            attached_to_id=target_id,
            actor_id=actor_id,
        )

    assert _attached_kind_counts(conn, table="decisions") == {
        "job": 1,
        "pipeline": 1,
        "project": 1,
    }
    assert _attached_kind_counts(conn, table="learnings") == {
        "job": 1,
        "pipeline": 1,
        "project": 1,
    }


@pytest.mark.asyncio
async def test_submit_job_only_creates_job_attached_decisions_and_learnings(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=_done_payload(),
    )

    assert response.status_code == 200
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT table_name, attached_to_kind, attached_to_id
            FROM (
                SELECT 'decisions' AS table_name, attached_to_kind, attached_to_id
                FROM decisions
                UNION ALL
                SELECT 'learnings' AS table_name, attached_to_kind, attached_to_id
                FROM learnings
            ) rows
            ORDER BY table_name
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]

    assert rows == [
        {
            "table_name": "decisions",
            "attached_to_kind": "job",
            "attached_to_id": job_id,
        },
        {
            "table_name": "learnings",
            "attached_to_kind": "job",
            "attached_to_id": job_id,
        },
    ]
