from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models import Decision, InheritanceReferenceLists, Learning
from aq_api.models.db import Decision as DbDecision
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Learning as DbLearning
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject
from aq_api.models.decisions import AttachedToKind

AttachedEntityKind = Literal["project", "pipeline", "job"]
AttachmentScope = tuple[AttachedToKind, UUID]


@dataclass(frozen=True)
class AttachedChain:
    project_id: UUID
    pipeline_id: UUID | None = None
    job_id: UUID | None = None


async def _resolve_attached_chain(
    session: AsyncSession,
    *,
    entity_kind: AttachedEntityKind,
    entity_id: UUID,
) -> AttachedChain | None:
    if entity_kind == "project":
        project = await session.get(DbProject, entity_id)
        if project is None:
            return None
        return AttachedChain(project_id=project.id)

    if entity_kind == "pipeline":
        pipeline = await session.get(DbPipeline, entity_id)
        if pipeline is None:
            return None
        return AttachedChain(project_id=pipeline.project_id, pipeline_id=pipeline.id)

    job = await session.get(DbJob, entity_id)
    if job is None:
        return None
    return AttachedChain(
        project_id=job.project_id,
        pipeline_id=job.pipeline_id,
        job_id=job.id,
    )


def decision_learning_scopes_for_entity(
    *,
    entity_kind: AttachedEntityKind,
    chain: AttachedChain,
) -> tuple[list[AttachmentScope], list[AttachmentScope]]:
    if entity_kind == "project":
        return [("project", chain.project_id)], []

    if entity_kind == "pipeline":
        assert chain.pipeline_id is not None
        return [("pipeline", chain.pipeline_id)], [("project", chain.project_id)]

    assert chain.job_id is not None
    assert chain.pipeline_id is not None
    return [
        ("job", chain.job_id),
    ], [
        ("pipeline", chain.pipeline_id),
        ("project", chain.project_id),
    ]


def _decision_payload(decision: DbDecision) -> dict[str, object]:
    return cast(
        dict[str, object],
        Decision(
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
        ).model_dump(mode="json"),
    )


def _learning_payload(learning: DbLearning) -> dict[str, object]:
    return cast(
        dict[str, object],
        Learning(
            id=learning.id,
            attached_to_kind=cast(AttachedToKind, learning.attached_to_kind),
            attached_to_id=learning.attached_to_id,
            title=learning.title,
            statement=learning.statement,
            context=learning.context,
            created_by_actor_id=learning.created_by_actor_id,
            created_at=learning.created_at,
            deactivated_at=learning.deactivated_at,
        ).model_dump(mode="json"),
    )


async def _decision_payloads_for_scopes(
    session: AsyncSession,
    scopes: list[AttachmentScope],
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for attached_to_kind, attached_to_id in scopes:
        statement = (
            select(DbDecision)
            .where(
                DbDecision.attached_to_kind == attached_to_kind,
                DbDecision.attached_to_id == attached_to_id,
                DbDecision.deactivated_at.is_(None),
            )
            .order_by(DbDecision.created_at.asc(), DbDecision.id.asc())
        )
        payloads.extend(
            _decision_payload(decision)
            for decision in (await session.scalars(statement)).all()
        )
    return payloads


async def _learning_payloads_for_scopes(
    session: AsyncSession,
    scopes: list[AttachmentScope],
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for attached_to_kind, attached_to_id in scopes:
        statement = (
            select(DbLearning)
            .where(
                DbLearning.attached_to_kind == attached_to_kind,
                DbLearning.attached_to_id == attached_to_id,
                DbLearning.deactivated_at.is_(None),
            )
            .order_by(DbLearning.created_at.asc(), DbLearning.id.asc())
        )
        payloads.extend(
            _learning_payload(learning)
            for learning in (await session.scalars(statement)).all()
        )
    return payloads


async def decision_learning_inheritance_lists(
    session: AsyncSession,
    *,
    direct_scopes: list[AttachmentScope],
    inherited_scopes: list[AttachmentScope],
) -> tuple[InheritanceReferenceLists, InheritanceReferenceLists]:
    decisions = InheritanceReferenceLists(
        direct=await _decision_payloads_for_scopes(session, direct_scopes),
        inherited=await _decision_payloads_for_scopes(session, inherited_scopes),
    )
    learnings = InheritanceReferenceLists(
        direct=await _learning_payloads_for_scopes(session, direct_scopes),
        inherited=await _learning_payloads_for_scopes(session, inherited_scopes),
    )
    return decisions, learnings


__all__ = [
    "AttachedChain",
    "AttachedEntityKind",
    "AttachmentScope",
    "_resolve_attached_chain",
    "decision_learning_inheritance_lists",
    "decision_learning_scopes_for_entity",
]
