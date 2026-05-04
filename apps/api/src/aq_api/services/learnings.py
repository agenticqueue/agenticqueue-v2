from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    EditLearningRequest,
    EditLearningResponse,
    GetLearningResponse,
    Learning,
    ListLearningsResponse,
    SubmitLearningRequest,
    SubmitLearningResponse,
)
from aq_api.models.db import Learning as DbLearning
from aq_api.models.decisions import AttachedToKind
from aq_api.services._artifacts import (
    DEFAULT_ARTIFACT_LIST_LIMIT,
    bounded_artifact_limit,
    decode_artifact_cursor,
    encode_artifact_cursor,
    validate_attached_target,
)

SUBMIT_LEARNING_OP = "submit_learning"
EDIT_LEARNING_OP = "edit_learning"
LEARNING_TARGET_KIND = "learning"


class LearningNotFoundError(Exception):
    pass


def learning_from_db(learning: DbLearning) -> Learning:
    return Learning(
        id=learning.id,
        attached_to_kind=cast(AttachedToKind, learning.attached_to_kind),
        attached_to_id=learning.attached_to_id,
        title=learning.title,
        statement=learning.statement,
        context=learning.context,
        created_by_actor_id=learning.created_by_actor_id,
        created_at=learning.created_at,
        deactivated_at=learning.deactivated_at,
    )


async def submit_learning(
    session: AsyncSession,
    request: SubmitLearningRequest,
    *,
    actor_id: UUID,
) -> SubmitLearningResponse:
    response: SubmitLearningResponse | None = None
    async with audited_op(
        session,
        op=SUBMIT_LEARNING_OP,
        target_kind=LEARNING_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        await validate_attached_target(
            session,
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
        )
        db_learning = DbLearning(
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
            title=request.title,
            statement=request.statement,
            context=request.context,
            created_by_actor_id=actor_id,
        )
        session.add(db_learning)
        await session.flush()

        response = SubmitLearningResponse(learning=learning_from_db(db_learning))
        audit.target_id = db_learning.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def get_learning(
    session: AsyncSession,
    learning_id: UUID,
) -> GetLearningResponse:
    db_learning = await session.get(DbLearning, learning_id)
    if db_learning is None:
        raise LearningNotFoundError("learning not found")
    return GetLearningResponse(learning=learning_from_db(db_learning), visuals=[])


async def list_learnings(
    session: AsyncSession,
    *,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_ARTIFACT_LIST_LIMIT,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListLearningsResponse:
    bounded_limit = bounded_artifact_limit(limit)
    statement = select(DbLearning)

    if attached_to_kind is not None:
        statement = statement.where(DbLearning.attached_to_kind == attached_to_kind)
    if attached_to_id is not None:
        statement = statement.where(DbLearning.attached_to_id == attached_to_id)
    if actor_id is not None:
        statement = statement.where(DbLearning.created_by_actor_id == actor_id)
    if since is not None:
        statement = statement.where(DbLearning.created_at >= since)
    if not include_deactivated:
        statement = statement.where(DbLearning.deactivated_at.is_(None))
    if cursor is not None:
        created_at, learning_id = decode_artifact_cursor(cursor)
        statement = statement.where(
            or_(
                DbLearning.created_at < created_at,
                and_(
                    DbLearning.created_at == created_at,
                    DbLearning.id < learning_id,
                ),
            )
        )

    statement = statement.order_by(
        DbLearning.created_at.desc(),
        DbLearning.id.desc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_artifact_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if len(rows) > bounded_limit
        else None
    )
    return ListLearningsResponse(
        items=[learning_from_db(learning) for learning in page_rows],
        next_cursor=next_cursor,
    )


async def edit_learning(
    session: AsyncSession,
    learning_id: UUID,
    request: EditLearningRequest,
    *,
    actor_id: UUID,
) -> EditLearningResponse:
    response: EditLearningResponse | None = None
    request_payload = {
        "learning_id": str(learning_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=EDIT_LEARNING_OP,
        target_kind=LEARNING_TARGET_KIND,
        target_id=learning_id,
        request_payload=request_payload,
    ) as audit:
        db_learning = await session.get(DbLearning, learning_id)
        if db_learning is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="learning_not_found",
                message="learning not found",
                details={"learning_id": str(learning_id)},
            )

        if db_learning.created_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="learning_edit_forbidden",
                message="only the learning creator can edit it",
                details={
                    "actor_id": str(actor_id),
                    "created_by_actor_id": str(db_learning.created_by_actor_id),
                    "learning_id": str(learning_id),
                },
            )

        if "title" in request.model_fields_set and request.title is not None:
            db_learning.title = request.title
        if "statement" in request.model_fields_set and request.statement is not None:
            db_learning.statement = request.statement
        if "context" in request.model_fields_set:
            db_learning.context = request.context

        await session.flush()
        response = EditLearningResponse(learning=learning_from_db(db_learning))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
