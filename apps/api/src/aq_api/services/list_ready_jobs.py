from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models import JobState, ListReadyJobsResponse
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.labels import LabelName
from aq_api.services.jobs import decode_job_cursor, encode_job_cursor, job_from_db

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
READY_STATE: JobState = "ready"


class InvalidReadyJobCursorError(Exception):
    pass


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        return decode_job_cursor(cursor)
    except Exception as exc:
        raise InvalidReadyJobCursorError("invalid ready job cursor") from exc


async def list_ready_jobs(
    session: AsyncSession,
    *,
    project_id: UUID,
    label_filter: list[LabelName] | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
) -> ListReadyJobsResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    labels = label_filter or []

    statement = (
        select(DbJob)
        .join(DbPipeline, DbJob.pipeline_id == DbPipeline.id)
        .where(
            DbJob.state == READY_STATE,
            DbJob.project_id == project_id,
            DbPipeline.is_template.is_(False),
            DbPipeline.archived_at.is_(None),
        )
    )
    if labels:
        statement = statement.where(DbJob.labels.contains(labels))

    if cursor is not None:
        created_at, job_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                DbJob.created_at > created_at,
                and_(
                    DbJob.created_at == created_at,
                    DbJob.id > job_id,
                ),
            )
        )

    statement = statement.order_by(DbJob.created_at.asc(), DbJob.id.asc()).limit(
        bounded_limit + 1
    )
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_job_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListReadyJobsResponse(
        jobs=[job_from_db(job) for job in page_rows],
        next_cursor=next_cursor,
    )
