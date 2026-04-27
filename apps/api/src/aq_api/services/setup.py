import secrets

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models import SetupResponse
from aq_api.models.db import Actor as DbActor
from aq_api.models.db import ApiKey as DbApiKey
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)

SETUP_LOCK_KEY = "aq:setup-singleton"
SETUP_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('aq:setup-singleton'))"
FOUNDER_ACTOR_NAME = "founder"
FOUNDER_KEY_NAME = "founder"


class AlreadySetupError(Exception):
    pass


async def acquire_setup_lock(session: AsyncSession) -> None:
    await session.execute(text(SETUP_LOCK_SQL))


def generate_founder_key() -> str:
    return f"aq2_{secrets.token_urlsafe(32)}"


async def _actors_exist(session: AsyncSession) -> bool:
    result = await session.scalar(select(exists().where(DbActor.id.is_not(None))))
    return bool(result)


async def run_setup(session: AsyncSession) -> SetupResponse:
    async with session.begin():
        await acquire_setup_lock(session)

        if await _actors_exist(session):
            raise AlreadySetupError

        founder_key = generate_founder_key()
        founder = DbActor(name=FOUNDER_ACTOR_NAME, kind="human")
        session.add(founder)
        await session.flush()

        session.add(
            DbApiKey(
                actor_id=founder.id,
                name=FOUNDER_KEY_NAME,
                key_hash=PASSWORD_HASHER.hash(founder_key),
                prefix=founder_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id=lookup_id_for_key(founder_key),
            )
        )
        await session.flush()

        return SetupResponse(actor_id=founder.id, founder_key=founder_key)
