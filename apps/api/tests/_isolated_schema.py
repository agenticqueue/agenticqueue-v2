from __future__ import annotations

from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_CAP04_TABLES = (
    "actors",
    "api_keys",
    "audit_log",
    "projects",
    "labels",
    "pipelines",
    "jobs",
    "job_edges",
    "job_comments",
    "decisions",
    "learnings",
    "objectives",
    "visuals",
    "components",
)


def sync_conninfo(database_url_sync: str) -> str:
    return database_url_sync.replace("postgresql+psycopg://", "postgresql://", 1)


def create_cap04_schema(conninfo: str, *, prefix: str = "cap04_seed") -> str:
    schema = f"{prefix}_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
            )
            for table in _CAP04_TABLES:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE {}.{}
                        (
                            LIKE public.{}
                            INCLUDING DEFAULTS
                            INCLUDING CONSTRAINTS
                            INCLUDING INDEXES
                            INCLUDING GENERATED
                            INCLUDING IDENTITY
                        )
                        """
                    ).format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(table),
                    )
                )
            cursor.execute(
                """
                SELECT pg_class.relname, pg_constraint.conname
                FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_constraint.contype = 'f'
                  AND pg_namespace.nspname = %s
                """,
                (schema,),
            )
            for table_name, constraint_name in cursor.fetchall():
                cursor.execute(
                    sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {}").format(
                        sql.Identifier(schema),
                        sql.Identifier(str(table_name)),
                        sql.Identifier(str(constraint_name)),
                    )
                )
    return schema


def drop_schema(conninfo: str, schema: str) -> None:
    with psycopg.connect(conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def connect_in_schema(
    conninfo: str,
    schema: str,
) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        conninfo,
        autocommit=True,
        options=f"-csearch_path={schema},public",
    )


def create_async_engine_in_schema(database_url: str, schema: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "search_path": f"{schema},public",
            }
        },
    )
