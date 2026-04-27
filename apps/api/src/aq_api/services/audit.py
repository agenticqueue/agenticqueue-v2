import re
from collections.abc import Mapping
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._request_context import (
    get_authenticated_actor_id,
    get_claimed_actor_identity,
)
from aq_api.models.db import AuditLogEntry

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
SECRET_FIELD_RE = re.compile(r"(?i)(^|_)(key|token|secret|password|hash)(_|$)")


def redact_secrets(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        redacted: dict[str, JsonValue] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_FIELD_RE.search(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_secrets(item)
        return redacted

    if isinstance(value, list | tuple):
        return [redact_secrets(item) for item in value]

    if isinstance(value, str | int | float | bool) or value is None:
        return value

    return str(value)


def _payload(value: Mapping[str, object] | None) -> dict[str, object]:
    redacted = redact_secrets(dict(value or {}))
    return cast(dict[str, object], redacted)


async def record(
    session: AsyncSession,
    *,
    op: str,
    target_kind: str | None,
    target_id: UUID | None,
    request_payload: Mapping[str, object] | None = None,
    response_payload: Mapping[str, object] | None = None,
    error_code: str | None = None,
) -> AuditLogEntry:
    actor_id = get_authenticated_actor_id()
    if actor_id is None:
        raise RuntimeError("authenticated_actor_id is required to record audit")

    entry = AuditLogEntry(
        op=op,
        authenticated_actor_id=actor_id,
        claimed_actor_identity=get_claimed_actor_identity(),
        target_kind=target_kind,
        target_id=target_id,
        request_payload=_payload(request_payload),
        response_payload=(
            _payload(response_payload) if response_payload is not None else None
        ),
        error_code=error_code,
    )
    session.add(entry)
    await session.flush()
    return entry
