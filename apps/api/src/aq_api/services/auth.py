import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models.db import Actor as DbActor
from aq_api.models.db import ApiKey as DbApiKey

DISPLAY_PREFIX_LENGTH = 8
LOOKUP_ID_BYTES = 16
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)


def lookup_id_for_key(key: str, secret: str | None = None) -> bytes:
    if secret is None:
        from aq_api._settings import settings

        secret = settings.key_lookup_secret

    return hmac.new(
        secret.encode("utf-8"),
        key.encode("utf-8"),
        hashlib.sha256,
    ).digest()[:LOOKUP_ID_BYTES]


def _verify_key_hash(key_hash: str, key: str) -> bool:
    try:
        return bool(PASSWORD_HASHER.verify(key_hash, key))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


async def resolve_actor(session: AsyncSession, key: str) -> DbActor | None:
    if len(key) < DISPLAY_PREFIX_LENGTH:
        return None

    statement = (
        select(DbApiKey, DbActor)
        .join(DbActor, DbApiKey.actor_id == DbActor.id)
        .where(
            DbApiKey.lookup_id == lookup_id_for_key(key),
            DbApiKey.revoked_at.is_(None),
            DbActor.deactivated_at.is_(None),
        )
        .limit(1)
    )
    row = (await session.execute(statement)).tuples().one_or_none()
    if row is None:
        return None

    api_key, actor = row
    if _verify_key_hash(api_key.key_hash, key):
        return actor

    return None
