import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    CommentOnJobRequest,
    CommentOnJobResponse,
    JobComment,
    ListJobCommentsResponse,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import JobComment as DbJobComment

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
COMMENT_ON_JOB_OP = "comment_on_job"
JOB_TARGET_KIND = "job"


class InvalidJobCommentCursorError(Exception):
    pass


class JobCommentJobNotFoundError(Exception):
    pass


def job_comment_from_db(comment: DbJobComment) -> JobComment:
    return JobComment(
        id=comment.id,
        job_id=comment.job_id,
        author_actor_id=comment.author_actor_id,
        body=comment.body,
        created_at=comment.created_at,
    )


def encode_job_comment_cursor(comment: DbJobComment) -> str:
    payload = json.dumps(
        {
            "created_at": comment.created_at.isoformat(),
            "id": str(comment.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_job_comment_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        comment_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidJobCommentCursorError("invalid job comment cursor") from exc
    return created_at, comment_id


async def comment_on_job(
    session: AsyncSession,
    job_id: UUID,
    request: CommentOnJobRequest,
    *,
    actor_id: UUID,
) -> CommentOnJobResponse:
    response: CommentOnJobResponse | None = None
    body_length = len(request.body)
    request_payload = {"job_id": str(job_id), "body_length": body_length}
    async with audited_op(
        session,
        op=COMMENT_ON_JOB_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=request_payload,
    ) as audit:
        db_job = await session.get(DbJob, job_id)
        if db_job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        db_comment = DbJobComment(
            job_id=job_id,
            author_actor_id=actor_id,
            body=request.body,
        )
        session.add(db_comment)
        await session.flush()

        response = CommentOnJobResponse(comment=job_comment_from_db(db_comment))
        audit.response_payload = {
            "comment_id": str(db_comment.id),
            "body_length": body_length,
        }

    assert response is not None
    return response


async def list_job_comments(
    session: AsyncSession,
    job_id: UUID,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
) -> ListJobCommentsResponse:
    db_job = await session.get(DbJob, job_id)
    if db_job is None:
        raise JobCommentJobNotFoundError("job not found")

    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbJobComment).where(DbJobComment.job_id == job_id)
    if cursor is not None:
        created_at, comment_id = decode_job_comment_cursor(cursor)
        statement = statement.where(
            or_(
                DbJobComment.created_at > created_at,
                and_(
                    DbJobComment.created_at == created_at,
                    DbJobComment.id > comment_id,
                ),
            )
        )

    statement = statement.order_by(
        DbJobComment.created_at.asc(),
        DbJobComment.id.asc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_job_comment_cursor(page_rows[-1])
        if len(rows) > bounded_limit
        else None
    )
    return ListJobCommentsResponse(
        comments=[job_comment_from_db(comment) for comment in page_rows],
        next_cursor=next_cursor,
    )
