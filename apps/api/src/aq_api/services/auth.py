from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models.db import Actor as DbActor
from aq_api.models.db import ApiKey as DbApiKey

PREFIX_LENGTH = 8
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)


def key_prefix(key: str) -> str:
    return key[:PREFIX_LENGTH]


def _verify_key_hash(key_hash: str, key: str) -> bool:
    try:
        return bool(PASSWORD_HASHER.verify(key_hash, key))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


async def resolve_actor(session: AsyncSession, key: str) -> DbActor | None:
    if len(key) < PREFIX_LENGTH:
        return None

    statement = (
        select(DbApiKey, DbActor)
        .join(DbActor, DbApiKey.actor_id == DbActor.id)
        .where(
            DbApiKey.prefix == key_prefix(key),
            DbApiKey.revoked_at.is_(None),
            DbActor.deactivated_at.is_(None),
        )
        .order_by(DbApiKey.created_at.asc(), DbApiKey.id.asc())
    )
    rows = (await session.execute(statement)).tuples().all()

    for api_key, actor in rows:
        if _verify_key_hash(api_key.key_hash, key):
            return actor

    return None
