from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg import Connection


def insert_objective(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    created_by_actor_id: UUID,
    statement: str,
    metric: str | None = None,
    target_value: str | None = None,
    created_at_offset_seconds: int = 0,
    deactivated: bool = False,
) -> UUID:
    created_at = datetime.now().astimezone() + timedelta(
        seconds=created_at_offset_seconds
    )
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO objectives
                (
                    attached_to_kind,
                    attached_to_id,
                    statement,
                    metric,
                    target_value,
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
                statement,
                metric,
                target_value,
                created_by_actor_id,
                created_at,
                created_at if deactivated else None,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    objective_id = row[0]
    assert isinstance(objective_id, UUID)
    return objective_id


def insert_component(
    conn: Connection[tuple[object, ...]],
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
    created_by_actor_id: UUID,
    name: str,
    purpose: str | None = None,
    access_path: str = "mcp__example__tool",
    created_at_offset_seconds: int = 0,
    deactivated: bool = False,
) -> UUID:
    created_at = datetime.now().astimezone() + timedelta(
        seconds=created_at_offset_seconds
    )
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO components
                (
                    attached_to_kind,
                    attached_to_id,
                    name,
                    purpose,
                    access_path,
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
                name,
                purpose,
                access_path,
                created_by_actor_id,
                created_at,
                created_at if deactivated else None,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    component_id = row[0]
    assert isinstance(component_id, UUID)
    return component_id


def objective_row(
    conn: Connection[tuple[object, ...]],
    objective_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, statement, metric,
                   target_value, due_at, created_by_actor_id, created_at,
                   deactivated_at
            FROM objectives
            WHERE id = %s
            """,
            (objective_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)


def component_row(
    conn: Connection[tuple[object, ...]],
    component_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, attached_to_kind, attached_to_id, name, purpose,
                   access_path, created_by_actor_id, created_at, deactivated_at
            FROM components
            WHERE id = %s
            """,
            (component_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)
