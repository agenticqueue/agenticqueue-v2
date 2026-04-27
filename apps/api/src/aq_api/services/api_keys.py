from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import RevokeApiKeyResponse
from aq_api.models.db import ApiKey as DbApiKey
from aq_api.services.actors import api_key_from_db

REVOKE_API_KEY_OP = "revoke_api_key"


async def _api_key_by_id(
    session: AsyncSession,
    api_key_id: UUID,
) -> DbApiKey | None:
    return await session.get(DbApiKey, api_key_id)


async def _active_actor_keys_for_update(
    session: AsyncSession,
    actor_id: UUID,
) -> list[DbApiKey]:
    statement = (
        select(DbApiKey)
        .where(DbApiKey.actor_id == actor_id, DbApiKey.revoked_at.is_(None))
        .order_by(DbApiKey.created_at.asc(), DbApiKey.id.asc())
        .with_for_update()
    )
    return list((await session.scalars(statement)).all())


async def _raise_audited_denial(
    session: AsyncSession,
    *,
    api_key_id: UUID,
    error_code: str,
    status_code: int,
) -> NoReturn:
    async with audited_op(
        session,
        op=REVOKE_API_KEY_OP,
        target_kind="api_key",
        target_id=api_key_id,
        request_payload={"api_key_id": str(api_key_id)},
    ):
        raise BusinessRuleException(
            status_code=status_code,
            error_code=error_code,
            message=error_code,
        )


async def revoke_api_key(
    session: AsyncSession,
    *,
    actor_id: UUID,
    api_key_id: UUID,
) -> RevokeApiKeyResponse:
    target = await _api_key_by_id(session, api_key_id)
    if target is None:
        await _raise_audited_denial(
            session,
            api_key_id=api_key_id,
            error_code="api_key_not_found",
            status_code=404,
        )

    assert target is not None

    if target.actor_id != actor_id:
        await _raise_audited_denial(
            session,
            api_key_id=api_key_id,
            error_code="forbidden",
            status_code=403,
        )

    if target.revoked_at is not None:
        return RevokeApiKeyResponse(api_key=api_key_from_db(target))

    response: RevokeApiKeyResponse | None = None
    async with audited_op(
        session,
        op=REVOKE_API_KEY_OP,
        target_kind="api_key",
        target_id=api_key_id,
        request_payload={"api_key_id": str(api_key_id)},
    ) as audit:
        active_keys = await _active_actor_keys_for_update(session, actor_id)
        active_key_ids = {api_key.id for api_key in active_keys}

        if api_key_id not in active_key_ids:
            await session.refresh(target)
            response = RevokeApiKeyResponse(api_key=api_key_from_db(target))
            audit.response_payload = response.model_dump(mode="json")
            return response

        if len(active_keys) <= 1:
            raise BusinessRuleException(
                status_code=409,
                error_code="cannot_revoke_last_key",
                message="cannot revoke last active key",
            )

        target.revoked_at = datetime.now(UTC)
        target.revoked_by_actor_id = actor_id
        await session.flush()

        response = RevokeApiKeyResponse(api_key=api_key_from_db(target))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
