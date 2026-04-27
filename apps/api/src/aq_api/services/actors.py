import base64
import json
import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    Actor,
    ApiKey,
    CreateActorRequest,
    CreateActorResponse,
    ListActorsResponse,
    WhoamiResponse,
)
from aq_api.models.db import Actor as DbActor
from aq_api.models.db import ApiKey as DbApiKey
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CREATE_ACTOR_OP = "create_actor"


class InvalidCursorError(Exception):
    pass


def generate_actor_key() -> str:
    return f"aq2_{secrets.token_urlsafe(32)}"


def actor_from_db(actor: DbActor) -> Actor:
    return Actor(
        id=actor.id,
        name=actor.name,
        kind=actor.kind,  # type: ignore[arg-type]
        created_at=actor.created_at,
        deactivated_at=actor.deactivated_at,
    )


def api_key_from_db(api_key: DbApiKey) -> ApiKey:
    return ApiKey(
        id=api_key.id,
        actor_id=api_key.actor_id,
        name=api_key.name,
        prefix=api_key.prefix,
        created_at=api_key.created_at,
        revoked_at=api_key.revoked_at,
    )


def get_self(actor: DbActor) -> WhoamiResponse:
    return WhoamiResponse(actor=actor_from_db(actor))


async def get_self_by_id(session: AsyncSession, actor_id: UUID) -> WhoamiResponse:
    actor = await session.get(DbActor, actor_id)
    if actor is None or actor.deactivated_at is not None:
        raise RuntimeError("authenticated actor no longer exists")
    return get_self(actor)


def encode_actor_cursor(actor: DbActor) -> str:
    payload = json.dumps(
        {
            "created_at": actor.created_at.isoformat(),
            "id": str(actor.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_actor_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        actor_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidCursorError("invalid actor cursor") from exc
    return created_at, actor_id


async def list_actors(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListActorsResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbActor)

    if not include_deactivated:
        statement = statement.where(DbActor.deactivated_at.is_(None))

    if cursor is not None:
        created_at, actor_id = decode_actor_cursor(cursor)
        statement = statement.where(
            or_(
                DbActor.created_at > created_at,
                and_(DbActor.created_at == created_at, DbActor.id > actor_id),
            )
        )

    statement = statement.order_by(DbActor.created_at.asc(), DbActor.id.asc()).limit(
        bounded_limit + 1
    )
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_actor_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListActorsResponse(
        actors=[actor_from_db(actor) for actor in page_rows],
        next_cursor=next_cursor,
    )


async def _active_actor_id_by_name(session: AsyncSession, name: str) -> UUID | None:
    actor_id: UUID | None = await session.scalar(
        select(DbActor.id)
        .where(DbActor.name == name, DbActor.deactivated_at.is_(None))
        .limit(1)
    )
    return actor_id


async def create_actor(
    session: AsyncSession,
    request: CreateActorRequest,
) -> CreateActorResponse:
    response: CreateActorResponse | None = None
    async with audited_op(
        session,
        op=CREATE_ACTOR_OP,
        target_kind="actor",
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        existing_id = await _active_actor_id_by_name(session, request.name)
        if existing_id is not None:
            audit.target_id = existing_id
            raise BusinessRuleException(
                status_code=409,
                error_code="actor_already_exists",
                message="actor already exists",
            )

        plaintext_key = generate_actor_key()
        db_actor = DbActor(name=request.name, kind=request.kind)
        session.add(db_actor)
        await session.flush()

        db_api_key = DbApiKey(
            actor_id=db_actor.id,
            name=request.key_name,
            key_hash=PASSWORD_HASHER.hash(plaintext_key),
            prefix=plaintext_key[:DISPLAY_PREFIX_LENGTH],
            lookup_id=lookup_id_for_key(plaintext_key),
        )
        session.add(db_api_key)
        await session.flush()

        response = CreateActorResponse(
            actor=actor_from_db(db_actor),
            api_key=api_key_from_db(db_api_key),
            key=plaintext_key,
        )
        audit.target_id = db_actor.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
