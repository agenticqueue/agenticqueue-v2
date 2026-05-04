from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    CreateObjectiveRequest,
    CreateObjectiveResponse,
    GetObjectiveResponse,
    ListObjectivesResponse,
    Objective,
    UpdateObjectiveRequest,
    UpdateObjectiveResponse,
)
from aq_api.models.db import Objective as DbObjective
from aq_api.models.objectives import ObjectiveAttachedToKind
from aq_api.services._artifacts import (
    DEFAULT_ARTIFACT_LIST_LIMIT,
    bounded_artifact_limit,
    decode_artifact_cursor,
    encode_artifact_cursor,
    validate_attached_target,
)

CREATE_OBJECTIVE_OP = "create_objective"
UPDATE_OBJECTIVE_OP = "update_objective"
OBJECTIVE_TARGET_KIND = "objective"


class ObjectiveNotFoundError(Exception):
    pass


def objective_from_db(objective: DbObjective) -> Objective:
    return Objective(
        id=objective.id,
        attached_to_kind=cast(ObjectiveAttachedToKind, objective.attached_to_kind),
        attached_to_id=objective.attached_to_id,
        statement=objective.statement,
        metric=objective.metric,
        target_value=objective.target_value,
        due_at=objective.due_at,
        created_by_actor_id=objective.created_by_actor_id,
        created_at=objective.created_at,
        deactivated_at=objective.deactivated_at,
    )


async def create_objective(
    session: AsyncSession,
    request: CreateObjectiveRequest,
    *,
    actor_id: UUID,
) -> CreateObjectiveResponse:
    response: CreateObjectiveResponse | None = None
    async with audited_op(
        session,
        op=CREATE_OBJECTIVE_OP,
        target_kind=OBJECTIVE_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        await validate_attached_target(
            session,
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
        )
        db_objective = DbObjective(
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
            statement=request.statement,
            metric=request.metric,
            target_value=request.target_value,
            due_at=request.due_at,
            created_by_actor_id=actor_id,
        )
        session.add(db_objective)
        await session.flush()

        response = CreateObjectiveResponse(objective=objective_from_db(db_objective))
        audit.target_id = db_objective.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def get_objective(
    session: AsyncSession,
    objective_id: UUID,
) -> GetObjectiveResponse:
    db_objective = await session.get(DbObjective, objective_id)
    if db_objective is None:
        raise ObjectiveNotFoundError("objective not found")
    return GetObjectiveResponse(objective=objective_from_db(db_objective))


async def list_objectives(
    session: AsyncSession,
    *,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_ARTIFACT_LIST_LIMIT,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListObjectivesResponse:
    bounded_limit = bounded_artifact_limit(limit)
    statement = select(DbObjective)

    if attached_to_kind is not None:
        statement = statement.where(DbObjective.attached_to_kind == attached_to_kind)
    if attached_to_id is not None:
        statement = statement.where(DbObjective.attached_to_id == attached_to_id)
    if actor_id is not None:
        statement = statement.where(DbObjective.created_by_actor_id == actor_id)
    if since is not None:
        statement = statement.where(DbObjective.created_at >= since)
    if not include_deactivated:
        statement = statement.where(DbObjective.deactivated_at.is_(None))
    if cursor is not None:
        created_at, objective_id = decode_artifact_cursor(cursor)
        statement = statement.where(
            or_(
                DbObjective.created_at < created_at,
                and_(
                    DbObjective.created_at == created_at,
                    DbObjective.id < objective_id,
                ),
            )
        )

    statement = statement.order_by(
        DbObjective.created_at.desc(),
        DbObjective.id.desc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_artifact_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if len(rows) > bounded_limit
        else None
    )
    return ListObjectivesResponse(
        items=[objective_from_db(objective) for objective in page_rows],
        next_cursor=next_cursor,
    )


async def update_objective(
    session: AsyncSession,
    objective_id: UUID,
    request: UpdateObjectiveRequest,
    *,
    actor_id: UUID,
) -> UpdateObjectiveResponse:
    response: UpdateObjectiveResponse | None = None
    request_payload = {
        "objective_id": str(objective_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=UPDATE_OBJECTIVE_OP,
        target_kind=OBJECTIVE_TARGET_KIND,
        target_id=objective_id,
        request_payload=request_payload,
    ) as audit:
        db_objective = await session.get(DbObjective, objective_id)
        if db_objective is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="objective_not_found",
                message="objective not found",
                details={"objective_id": str(objective_id)},
            )

        if db_objective.created_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="objective_update_forbidden",
                message="only the objective creator can update it",
                details={
                    "actor_id": str(actor_id),
                    "created_by_actor_id": str(db_objective.created_by_actor_id),
                    "objective_id": str(objective_id),
                },
            )

        if "statement" in request.model_fields_set and request.statement is not None:
            db_objective.statement = request.statement
        if "metric" in request.model_fields_set:
            db_objective.metric = request.metric
        if "target_value" in request.model_fields_set:
            db_objective.target_value = request.target_value
        if "due_at" in request.model_fields_set:
            db_objective.due_at = request.due_at

        await session.flush()
        response = UpdateObjectiveResponse(objective=objective_from_db(db_objective))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
