from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException
from aq_api._datetime import parse_utc
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject

DEFAULT_ARTIFACT_LIST_LIMIT = 50
MAX_ARTIFACT_LIST_LIMIT = 100


class InvalidArtifactCursorError(Exception):
    pass


def bounded_artifact_limit(limit: int) -> int:
    return min(max(limit, 1), MAX_ARTIFACT_LIST_LIMIT)


def encode_artifact_cursor(created_at: datetime, artifact_id: UUID) -> str:
    payload = json.dumps(
        {
            "created_at": created_at.isoformat(),
            "id": str(artifact_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_artifact_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        artifact_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidArtifactCursorError("invalid artifact cursor") from exc
    return created_at, artifact_id


async def validate_attached_target(
    session: AsyncSession,
    *,
    attached_to_kind: str,
    attached_to_id: UUID,
) -> None:
    model: type[DbProject] | type[DbPipeline] | type[DbJob]
    match attached_to_kind:
        case "project":
            model = DbProject
        case "pipeline":
            model = DbPipeline
        case "job":
            model = DbJob
        case _:
            raise BusinessRuleException(
                status_code=422,
                error_code="attached_target_kind_invalid",
                message="attached_to_kind must be project, pipeline, or job",
                details={"attached_to_kind": attached_to_kind},
            )

    target = await session.get(model, attached_to_id)
    if target is None:
        raise BusinessRuleException(
            status_code=404,
            error_code="attached_target_not_found",
            message="attached target not found",
            details={
                "attached_to_kind": attached_to_kind,
                "attached_to_id": str(attached_to_id),
            },
        )
