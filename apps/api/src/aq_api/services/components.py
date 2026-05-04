from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    Component,
    CreateComponentRequest,
    CreateComponentResponse,
    GetComponentResponse,
    ListComponentsResponse,
    UpdateComponentRequest,
    UpdateComponentResponse,
)
from aq_api.models.components import ComponentAttachedToKind
from aq_api.models.db import Component as DbComponent
from aq_api.services._artifacts import (
    DEFAULT_ARTIFACT_LIST_LIMIT,
    bounded_artifact_limit,
    decode_artifact_cursor,
    encode_artifact_cursor,
    validate_attached_target,
)

CREATE_COMPONENT_OP = "create_component"
UPDATE_COMPONENT_OP = "update_component"
COMPONENT_TARGET_KIND = "component"


class ComponentNotFoundError(Exception):
    pass


def component_from_db(component: DbComponent) -> Component:
    return Component(
        id=component.id,
        attached_to_kind=cast(ComponentAttachedToKind, component.attached_to_kind),
        attached_to_id=component.attached_to_id,
        name=component.name,
        purpose=component.purpose,
        access_path=component.access_path,
        created_by_actor_id=component.created_by_actor_id,
        created_at=component.created_at,
        deactivated_at=component.deactivated_at,
    )


async def create_component(
    session: AsyncSession,
    request: CreateComponentRequest,
    *,
    actor_id: UUID,
) -> CreateComponentResponse:
    response: CreateComponentResponse | None = None
    async with audited_op(
        session,
        op=CREATE_COMPONENT_OP,
        target_kind=COMPONENT_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        await validate_attached_target(
            session,
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
        )
        db_component = DbComponent(
            attached_to_kind=request.attached_to_kind,
            attached_to_id=request.attached_to_id,
            name=request.name,
            purpose=request.purpose,
            access_path=request.access_path,
            created_by_actor_id=actor_id,
        )
        session.add(db_component)
        await session.flush()

        response = CreateComponentResponse(component=component_from_db(db_component))
        audit.target_id = db_component.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def get_component(
    session: AsyncSession,
    component_id: UUID,
) -> GetComponentResponse:
    db_component = await session.get(DbComponent, component_id)
    if db_component is None:
        raise ComponentNotFoundError("component not found")
    return GetComponentResponse(component=component_from_db(db_component))


async def list_components(
    session: AsyncSession,
    *,
    attached_to_kind: str | None = None,
    attached_to_id: UUID | None = None,
    actor_id: UUID | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_ARTIFACT_LIST_LIMIT,
    cursor: str | None = None,
    include_deactivated: bool = False,
) -> ListComponentsResponse:
    bounded_limit = bounded_artifact_limit(limit)
    statement = select(DbComponent)

    if attached_to_kind is not None:
        statement = statement.where(DbComponent.attached_to_kind == attached_to_kind)
    if attached_to_id is not None:
        statement = statement.where(DbComponent.attached_to_id == attached_to_id)
    if actor_id is not None:
        statement = statement.where(DbComponent.created_by_actor_id == actor_id)
    if since is not None:
        statement = statement.where(DbComponent.created_at >= since)
    if not include_deactivated:
        statement = statement.where(DbComponent.deactivated_at.is_(None))
    if cursor is not None:
        created_at, component_id = decode_artifact_cursor(cursor)
        statement = statement.where(
            or_(
                DbComponent.created_at < created_at,
                and_(
                    DbComponent.created_at == created_at,
                    DbComponent.id < component_id,
                ),
            )
        )

    statement = statement.order_by(
        DbComponent.created_at.desc(),
        DbComponent.id.desc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_artifact_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if len(rows) > bounded_limit
        else None
    )
    return ListComponentsResponse(
        items=[component_from_db(component) for component in page_rows],
        next_cursor=next_cursor,
    )


async def update_component(
    session: AsyncSession,
    component_id: UUID,
    request: UpdateComponentRequest,
    *,
    actor_id: UUID,
) -> UpdateComponentResponse:
    response: UpdateComponentResponse | None = None
    request_payload = {
        "component_id": str(component_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=UPDATE_COMPONENT_OP,
        target_kind=COMPONENT_TARGET_KIND,
        target_id=component_id,
        request_payload=request_payload,
    ) as audit:
        db_component = await session.get(DbComponent, component_id)
        if db_component is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="component_not_found",
                message="component not found",
                details={"component_id": str(component_id)},
            )

        if db_component.created_by_actor_id != actor_id:
            raise BusinessRuleException(
                status_code=403,
                error_code="component_update_forbidden",
                message="only the component creator can update it",
                details={
                    "actor_id": str(actor_id),
                    "created_by_actor_id": str(db_component.created_by_actor_id),
                    "component_id": str(component_id),
                },
            )

        if "name" in request.model_fields_set and request.name is not None:
            db_component.name = request.name
        if "purpose" in request.model_fields_set:
            db_component.purpose = request.purpose
        if (
            "access_path" in request.model_fields_set
            and request.access_path is not None
        ):
            db_component.access_path = request.access_path

        await session.flush()
        response = UpdateComponentResponse(component=component_from_db(db_component))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
