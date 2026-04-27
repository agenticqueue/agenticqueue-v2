import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import CheckViolation, UniqueViolation

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live schema tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        yield connection
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM api_keys WHERE name = 'test key'")
            cursor.execute("DELETE FROM actors WHERE name LIKE 'schema-%'")


def _insert_actor(conn: Connection[tuple[object, ...]], name: str) -> uuid.UUID:
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
    value = row[0]
    assert isinstance(value, uuid.UUID)
    return value


def test_schema_contains_required_tables_indexes_and_checks(
    conn: Connection[tuple[object, ...]],
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('actors', 'api_keys', 'audit_log')
            """
        )
        tables = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN ('actors', 'api_keys', 'audit_log')
            """
        )
        indexes = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid IN ('actors'::regclass, 'api_keys'::regclass)
              AND contype = 'c'
            """
        )
        checks = {row[0] for row in cursor.fetchall()}

    assert {"actors", "api_keys", "audit_log"} <= tables
    assert {
        "actors_name_active_uniq",
        "api_keys_actor_active_idx",
        "api_keys_prefix_idx",
        "audit_log_ts_idx",
        "audit_log_actor_ts_idx",
        "audit_log_op_ts_idx",
        "audit_log_target_idx",
    } <= indexes
    assert {"actors_kind_check", "api_keys_revoked_fields_check"} <= checks


def test_partial_unique_index_allows_name_reuse_after_deactivation(
    conn: Connection[tuple[object, ...]],
) -> None:
    name = f"schema-dupe-{uuid.uuid4()}"
    actor_id = _insert_actor(conn, name)

    with pytest.raises(UniqueViolation):
        _insert_actor(conn, name)

    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE actors SET deactivated_at = now() WHERE id = %s",
            (actor_id,),
        )

    replacement_id = _insert_actor(conn, name)
    assert replacement_id != actor_id


def test_api_keys_reject_half_revoked_rows(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id = _insert_actor(conn, f"schema-key-{uuid.uuid4()}")

    with pytest.raises(CheckViolation):
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO api_keys
                    (actor_id, name, key_hash, prefix, revoked_at)
                VALUES
                    (%s, 'test key', 'schema-test-hash', 'abcdefgh', now())
                """,
                (actor_id,),
            )

    with pytest.raises(CheckViolation):
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO api_keys
                    (actor_id, name, key_hash, prefix, revoked_by_actor_id)
                VALUES
                    (%s, 'test key', 'schema-test-hash', 'abcdefgh', %s)
                """,
                (actor_id, actor_id),
            )
