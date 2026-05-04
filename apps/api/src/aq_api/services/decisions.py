from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    CreateDecisionRequest,
    CreateDecisionResponse,
    Decision,
    GetDecisionResponse,
    ListDecisionsResponse,
    SupersedeDecisionRequest,
    SupersedeDecisionResponse,
)
from aq_api.models.db import Decision as DbDecision
from aq_api.models.decisions import AttachedToKind
from aq_api.services._artifacts import (
    DEFAULT_ARTIFACT_LIST_LIMIT,
    bounded_artifact_limit,
    decode_artifact_cursor,
    encode_artifact_cursor,
    validate_attached_target,
)

CREATE_DECISION_OP = "create_decision"
SUPERSEDE_DECISION_OP = "supersede_decision"
DECISION_TARGET_KIND = "decision"


class DecisionNotFoundError(Exception):
    pass


def decision_from_db(decision: DbDecision) -> Decision:
    return Decision(
        id=decision.id,
        attached_to_kind=cast(AttachedToKind, decision.attached_to_kind),
        attached_to_id=decision.attached_to_id,
        title=decision.title,
        statement=decision.statement,
        rationale=decision.rationale,
        supersedes_decision_id=decision.supersedes_decision_id,
        created_by_actor_id=decision.created_by_actor_id,
        created_at=decision.created_at,
        deactivated_at=decision.deactivated_at,
    )


async def create_decision(
    session: AsyncSession,
    request: CreateDecisionRequest,
    *,
    actor_id: UUID,
) -> CreateDecisionResponse:
    response: CreateDecisionResponse | None = None
    async with audited_op(
        session,
        op=CREATE_DECISION_OP,
        target_kind=DECISION_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        await validate_attached_target(
            session,
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
        )
        db_decision = DbDecision(
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
            title=request.title,
            statement=request.statement,
            rationale=request.rationale,
            created_by_actor_id=actor_id,
        )
        session.add(db_decision)
        await session.flush()

        response = CreateDecisionResponse(decision=decision_from_db(db_decision))
        audit.target_id = db_decision.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def get_decision(
    session: AsyncSession,
    decision_id: UUID,
) -> GetDecisionResponse:
    db_decision = await session.get(DbDecision, decision_id)
    if db_decision is None:
        raise DecisionNotFoundError("decision not found")
    return GetDecisionResponse(decision=decision_from_db(db_decision), visuals=[])


async def list_decisions(
    session: AsyncSession,
    *,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_ARTIFACT_LIST_LIMIT,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListDecisionsResponse:
    bounded_limit = bounded_artifact_limit(limit)
    statement = select(DbDecision)

    if attached_to_kind is not None:
        statement = statement.where(DbDecision.attached_to_kind == attached_to_kind)
    if attached_to_id is not None:
        statement = statement.where(DbDecision.attached_to_id == attached_to_id)
    if actor_id is not None:
        statement = statement.where(DbDecision.created_by_actor_id == actor_id)
    if since is not None:
        statement = statement.where(DbDecision.created_at >= since)
    if not include_deactivated:
        statement = statement.where(DbDecision.deactivated_at.is_(None))
    if cursor is not None:
        created_at, decision_id = decode_artifact_cursor(cursor)
        statement = statement.where(
            or_(
                DbDecision.created_at < created_at,
                and_(
                    DbDecision.created_at == created_at,
                    DbDecision.id < decision_id,
                ),
            )
        )

    statement = statement.order_by(
        DbDecision.created_at.desc(),
        DbDecision.id.desc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_artifact_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if len(rows) > bounded_limit
        else None
    )
    return ListDecisionsResponse(
        items=[decision_from_db(decision) for decision in page_rows],
        next_cursor=next_cursor,
    )


async def supersede_decision(
    session: AsyncSession,
    decision_id: UUID,
    request: SupersedeDecisionRequest,
) -> SupersedeDecisionResponse:
    response: SupersedeDecisionResponse | None = None
    request_payload = {
        "decision_id": str(decision_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=SUPERSEDE_DECISION_OP,
        target_kind=DECISION_TARGET_KIND,
        target_id=decision_id,
        request_payload=request_payload,
    ) as audit:
        if decision_id == request.replacement_id:
            raise BusinessRuleException(
                status_code=409,
                error_code="self_supersede",
                message="decision cannot supersede itself",
                details={"decision_id": str(decision_id)},
            )

        old_decision = await session.get(DbDecision, decision_id)
        if old_decision is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="decision_not_found",
                message="decision not found",
                details={"decision_id": str(decision_id)},
            )
        replacement = await session.get(DbDecision, request.replacement_id)
        if replacement is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="replacement_decision_not_found",
                message="replacement decision not found",
                details={"replacement_id": str(request.replacement_id)},
            )

        deactivated_ids = [
            str(item.id)
            for item in (old_decision, replacement)
            if item.deactivated_at is not None
        ]
        if deactivated_ids:
            raise BusinessRuleException(
                status_code=409,
                error_code="decision_already_deactivated",
                message="both decisions must be active",
                details={"decision_ids": deactivated_ids},
            )

        if (
            old_decision.attached_to_kind != replacement.attached_to_kind
            or old_decision.attached_to_id != replacement.attached_to_id
        ):
            raise BusinessRuleException(
                status_code=409,
                error_code="supersede_scope_mismatch",
                message="decisions must share the same attachment scope",
                details={
                    "decision_id": str(decision_id),
                    "replacement_id": str(request.replacement_id),
                },
            )

        old_decision.deactivated_at = datetime.now(UTC)
        replacement.supersedes_decision_id = old_decision.id
        await session.flush()

        response = SupersedeDecisionResponse(
            old_decision=decision_from_db(old_decision),
            replacement_decision=decision_from_db(replacement),
        )
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
