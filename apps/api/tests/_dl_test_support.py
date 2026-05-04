from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg import Connection


def insert_decision(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    created_by_actor_id: UUID,
    title: str,
    statement: str | None = None,
    rationale: str | None = None,
    created_at_offset_seconds: int = 0,
    deactivated: bool = False,
    supersedes_decision_id: UUID | None = None,
) -> UUID:
    created_at = datetime.now().astimezone() + timedelta(
        seconds=created_at_offset_seconds
    )
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
                    supersedes_decision_id,
                    created_by_actor_id,
                    created_at,
                    deactivated_at
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                attached_to_kind,
                attached_to_id,
                title,
                statement or f"{title} statement",
                rationale,
                supersedes_decision_id,
                created_by_actor_id,
                created_at,
                created_at if deactivated else None,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    decision_id = row[0]
    assert isinstance(decision_id, UUID)
    return decision_id


def insert_learning(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    created_by_actor_id: UUID,
    title: str,
    statement: str | None = None,
    context: str | None = None,
    created_at_offset_seconds: int = 0,
    deactivated: bool = False,
) -> UUID:
    created_at = datetime.now().astimezone() + timedelta(
        seconds=created_at_offset_seconds
    )
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
                    created_by_actor_id,
                    created_at,
                    deactivated_at
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                attached_to_kind,
                attached_to_id,
                title,
                statement or f"{title} statement",
                context,
                created_by_actor_id,
                created_at,
                created_at if deactivated else None,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    learning_id = row[0]
    assert isinstance(learning_id, UUID)
    return learning_id


def decision_row(
    conn: Connection[tuple[object, ...]],
    decision_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, statement,
                   rationale, supersedes_decision_id, created_by_actor_id,
                   created_at, deactivated_at
            FROM decisions
            WHERE id = %s
            """,
            (decision_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)


def learning_row(
    conn: Connection[tuple[object, ...]],
    learning_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, title, statement,
                   context, created_by_actor_id, created_at, deactivated_at
            FROM learnings
            WHERE id = %s
            """,
            (learning_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)
