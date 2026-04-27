import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from aq_api.app import app
from aq_api.models import VersionInfo
from aq_api.services.auth import PASSWORD_HASHER
from fastapi.testclient import TestClient
from httpx import Response
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
UNAUTHENTICATED_BODY = b'{"error":"unauthenticated"}'

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live auth tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        yield connection
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM api_keys WHERE name LIKE 'auth-test-%'")
            cursor.execute("DELETE FROM actors WHERE name LIKE 'auth-test-%'")


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as api_client:
        yield api_client


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    key: str,
    *,
    actor_name: str | None = None,
    key_name: str | None = None,
    revoked: bool = False,
    deactivated: bool = False,
) -> UUID:
    actor_name = actor_name or f"auth-test-{uuid.uuid4()}"
    key_name = key_name or f"auth-test-{uuid.uuid4()}"
    deactivated_at = datetime.now(UTC) if deactivated else None
    revoked_at = datetime.now(UTC) if revoked else None

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind, deactivated_at)
            VALUES (%s, 'human', %s)
            RETURNING id
            """,
            (actor_name, deactivated_at),
        )
        actor_row = cursor.fetchone()
        assert actor_row is not None
        actor_id = actor_row[0]
        assert isinstance(actor_id, UUID)

        cursor.execute(
            """
            INSERT INTO api_keys
                (actor_id, name, key_hash, prefix, revoked_at, revoked_by_actor_id)
            VALUES
                (%s, %s, %s, %s, %s, %s)
            """,
            (
                actor_id,
                key_name,
                PASSWORD_HASHER.hash(key),
                key[:8],
                revoked_at,
                actor_id if revoked else None,
            ),
        )

    return actor_id


def _assert_unauthenticated(response: Response) -> None:
    assert response.status_code == 401
    assert response.content == UNAUTHENTICATED_BODY


def test_missing_authorization_header_returns_byte_equal_401(
    client: TestClient,
) -> None:
    _assert_unauthenticated(client.get("/version"))


def test_bogus_token_returns_byte_equal_401(
    client: TestClient,
) -> None:
    _assert_unauthenticated(
        client.get("/version", headers={"Authorization": "Bearer aq2bad_invalid"})
    )


def test_revoked_key_returns_byte_equal_401(
    conn: Connection[tuple[object, ...]],
    client: TestClient,
) -> None:
    key = "aq2revoked_contract_test_key"
    _insert_actor_with_key(conn, key, revoked=True)

    _assert_unauthenticated(
        client.get("/version", headers={"Authorization": f"Bearer {key}"})
    )


def test_key_for_deactivated_actor_returns_byte_equal_401(
    conn: Connection[tuple[object, ...]],
    client: TestClient,
) -> None:
    key = "aq2dead_actor_contract_key"
    _insert_actor_with_key(conn, key, deactivated=True)

    _assert_unauthenticated(
        client.get("/version", headers={"Authorization": f"Bearer {key}"})
    )


def test_revocation_is_live_without_result_cache(
    conn: Connection[tuple[object, ...]],
    client: TestClient,
) -> None:
    key = "aq2live_revocation_contract_key"
    actor_id = _insert_actor_with_key(conn, key)

    first = client.get("/version", headers={"Authorization": f"Bearer {key}"})
    assert first.status_code == 200
    VersionInfo.model_validate(first.json())

    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE api_keys
            SET revoked_at = now(), revoked_by_actor_id = %s
            WHERE actor_id = %s
            """,
            (actor_id, actor_id),
        )

    _assert_unauthenticated(
        client.get("/version", headers={"Authorization": f"Bearer {key}"})
    )


def test_prefix_collision_tries_every_candidate_before_rejecting(
    conn: Connection[tuple[object, ...]],
    client: TestClient,
) -> None:
    valid_key = "aq2same_valid_contract_key"
    colliding_key = "aq2same_other_contract_key"
    _insert_actor_with_key(conn, colliding_key)
    _insert_actor_with_key(conn, valid_key)

    response = client.get(
        "/version",
        headers={"Authorization": f"Bearer {valid_key}"},
    )

    assert response.status_code == 200
    VersionInfo.model_validate(response.json())


def test_mcp_http_without_bearer_matches_rest_401(client: TestClient) -> None:
    response = client.post("/mcp", json={})

    _assert_unauthenticated(response)
