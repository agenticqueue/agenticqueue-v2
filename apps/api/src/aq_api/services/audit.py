import base64
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._datetime import parse_utc
from aq_api._request_context import (
    get_authenticated_actor_id,
    get_claimed_actor_identity,
)
from aq_api.models import AuditLogEntry as AuditLogEntryModel
from aq_api.models import AuditLogPage, AuditQueryParams
from aq_api.models.db import AuditLogEntry as DbAuditLogEntry

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
SECRET_FIELD_RE = re.compile(r"(?i)(^|_)(key|token|secret|password|hash)(_|$)")
MAX_AUDIT_LIMIT = 200


class InvalidAuditCursorError(Exception):
    pass


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
) -> DbAuditLogEntry:
    actor_id = get_authenticated_actor_id()
    if actor_id is None:
        raise RuntimeError("authenticated_actor_id is required to record audit")

    entry = DbAuditLogEntry(
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


def audit_entry_from_db(entry: DbAuditLogEntry) -> AuditLogEntryModel:
    return AuditLogEntryModel(
        id=entry.id,
        ts=entry.ts,
        op=entry.op,
        authenticated_actor_id=entry.authenticated_actor_id,
        claimed_actor_identity=entry.claimed_actor_identity,
        target_kind=entry.target_kind,
        target_id=entry.target_id,
        request_payload=entry.request_payload,
        response_payload=entry.response_payload,
        error_code=entry.error_code,
    )


def encode_audit_cursor(entry: DbAuditLogEntry) -> str:
    payload = json.dumps(
        {
            "ts": entry.ts.isoformat(),
            "id": str(entry.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_audit_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        ts = parse_utc(str(payload["ts"]))
        entry_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidAuditCursorError("invalid audit cursor") from exc
    return ts, entry_id


def _actor_filter(actor: str | None) -> UUID | None | bool:
    if actor is None:
        return None
    try:
        return UUID(actor)
    except ValueError:
        return False


async def query_audit_log(
    session: AsyncSession,
    params: AuditQueryParams,
) -> AuditLogPage:
    actor_id = _actor_filter(params.actor)
    if actor_id is False:
        return AuditLogPage(entries=[], next_cursor=None)

    limit = min(max(params.limit, 1), MAX_AUDIT_LIMIT)
    statement = select(DbAuditLogEntry)

    if actor_id is not None:
        statement = statement.where(DbAuditLogEntry.authenticated_actor_id == actor_id)
    if params.op is not None:
        statement = statement.where(DbAuditLogEntry.op == params.op)
    if params.since is not None:
        statement = statement.where(DbAuditLogEntry.ts >= params.since)
    if params.until is not None:
        statement = statement.where(DbAuditLogEntry.ts <= params.until)
    if params.cursor is not None:
        cursor_ts, cursor_id = decode_audit_cursor(params.cursor)
        statement = statement.where(
            or_(
                DbAuditLogEntry.ts < cursor_ts,
                and_(
                    DbAuditLogEntry.ts == cursor_ts,
                    DbAuditLogEntry.id < cursor_id,
                ),
            )
        )

    statement = statement.order_by(
        DbAuditLogEntry.ts.desc(),
        DbAuditLogEntry.id.desc(),
    ).limit(limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:limit]
    next_cursor = encode_audit_cursor(page_rows[-1]) if len(rows) > limit else None
    return AuditLogPage(
        entries=[audit_entry_from_db(row) for row in page_rows],
        next_cursor=next_cursor,
    )
