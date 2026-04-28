import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    ArchiveWorkflowResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    GetWorkflowResponse,
    ListWorkflowsResponse,
    UpdateWorkflowRequest,
    UpdateWorkflowResponse,
    Workflow,
    WorkflowStep,
    WorkflowStepInput,
)
from aq_api.models.db import ContractProfile as DbContractProfile
from aq_api.models.db import Workflow as DbWorkflow
from aq_api.models.db import WorkflowStep as DbWorkflowStep

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CREATE_WORKFLOW_OP = "create_workflow"
UPDATE_WORKFLOW_OP = "update_workflow"
ARCHIVE_WORKFLOW_OP = "archive_workflow"
WORKFLOW_TARGET_KIND = "workflow"


class InvalidWorkflowCursorError(Exception):
    pass


class WorkflowNotFoundError(Exception):
    pass


def encode_workflow_cursor(workflow: DbWorkflow) -> str:
    payload = json.dumps(
        {
            "created_at": workflow.created_at.isoformat(),
            "id": str(workflow.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_workflow_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        workflow_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidWorkflowCursorError("invalid workflow cursor") from exc
    return created_at, workflow_id


def workflow_step_from_db(step: DbWorkflowStep) -> WorkflowStep:
    return WorkflowStep(
        id=step.id,
        workflow_id=step.workflow_id,
        name=step.name,
        ordinal=step.ordinal,
        default_contract_profile_id=step.default_contract_profile_id,
        step_edges=step.step_edges or {},
    )


async def workflow_from_db(
    session: AsyncSession,
    workflow: DbWorkflow,
) -> Workflow:
    steps = list(
        (
            await session.scalars(
                select(DbWorkflowStep)
                .where(DbWorkflowStep.workflow_id == workflow.id)
                .order_by(DbWorkflowStep.ordinal.asc(), DbWorkflowStep.id.asc())
            )
        ).all()
    )
    return Workflow(
        id=workflow.id,
        slug=workflow.slug,
        name=workflow.name,
        version=workflow.version,
        is_archived=workflow.is_archived,
        created_at=workflow.created_at,
        created_by_actor_id=workflow.created_by_actor_id,
        supersedes_workflow_id=workflow.supersedes_workflow_id,
        steps=[workflow_step_from_db(step) for step in steps],
    )


async def _assert_contract_profiles_exist(
    session: AsyncSession,
    steps: list[WorkflowStepInput],
) -> None:
    profile_ids = {step.default_contract_profile_id for step in steps}
    existing_ids = set(
        (
            await session.scalars(
                select(DbContractProfile.id).where(
                    DbContractProfile.id.in_(profile_ids)
                )
            )
        ).all()
    )
    if profile_ids != existing_ids:
        raise BusinessRuleException(
            status_code=404,
            error_code="contract_profile_not_found",
            message="contract profile not found",
        )


def _assert_unique_ordinals(steps: list[WorkflowStepInput]) -> None:
    ordinals = [step.ordinal for step in steps]
    if len(ordinals) != len(set(ordinals)):
        raise BusinessRuleException(
            status_code=409,
            error_code="workflow_step_ordinal_duplicate",
            message="workflow step ordinals must be unique",
        )


def _add_steps(
    session: AsyncSession,
    *,
    workflow_id: UUID,
    steps: list[WorkflowStepInput],
) -> None:
    for step in steps:
        session.add(
            DbWorkflowStep(
                workflow_id=workflow_id,
                name=step.name,
                ordinal=step.ordinal,
                default_contract_profile_id=step.default_contract_profile_id,
                step_edges=step.step_edges,
            )
        )


async def _workflow_id_by_slug(session: AsyncSession, slug: str) -> UUID | None:
    workflow_id: UUID | None = await session.scalar(
        select(DbWorkflow.id).where(DbWorkflow.slug == slug).limit(1)
    )
    return workflow_id


async def _latest_family_workflow(
    session: AsyncSession,
    *,
    slug: str,
    lock: bool = False,
) -> DbWorkflow | None:
    statement = (
        select(DbWorkflow)
        .where(DbWorkflow.slug == slug)
        .order_by(DbWorkflow.version.desc(), DbWorkflow.id.desc())
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    workflow: DbWorkflow | None = await session.scalar(statement)
    return workflow


async def list_workflows(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
    include_archived: bool = False,
) -> ListWorkflowsResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbWorkflow)

    if not include_archived:
        statement = statement.where(DbWorkflow.is_archived.is_(False))

    if cursor is not None:
        created_at, workflow_id = decode_workflow_cursor(cursor)
        statement = statement.where(
            or_(
                DbWorkflow.created_at > created_at,
                and_(
                    DbWorkflow.created_at == created_at,
                    DbWorkflow.id > workflow_id,
                ),
            )
        )

    statement = statement.order_by(
        DbWorkflow.created_at.asc(),
        DbWorkflow.id.asc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_workflow_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListWorkflowsResponse(
        workflows=[await workflow_from_db(session, workflow) for workflow in page_rows],
        next_cursor=next_cursor,
    )


async def get_workflow(
    session: AsyncSession,
    workflow_id: UUID,
) -> GetWorkflowResponse:
    workflow = await session.get(DbWorkflow, workflow_id)
    if workflow is None:
        raise WorkflowNotFoundError("workflow not found")
    return GetWorkflowResponse(workflow=await workflow_from_db(session, workflow))


async def create_workflow(
    session: AsyncSession,
    request: CreateWorkflowRequest,
    *,
    actor_id: UUID,
) -> CreateWorkflowResponse:
    response: CreateWorkflowResponse | None = None
    async with audited_op(
        session,
        op=CREATE_WORKFLOW_OP,
        target_kind=WORKFLOW_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        existing_id = await _workflow_id_by_slug(session, request.slug)
        if existing_id is not None:
            audit.target_id = existing_id
            raise BusinessRuleException(
                status_code=409,
                error_code="slug_taken",
                message="workflow slug already exists",
            )

        _assert_unique_ordinals(request.steps)
        await _assert_contract_profiles_exist(session, request.steps)

        db_workflow = DbWorkflow(
            slug=request.slug,
            name=request.name,
            version=1,
            created_by_actor_id=actor_id,
        )
        try:
            session.add(db_workflow)
            await session.flush()
            _add_steps(session, workflow_id=db_workflow.id, steps=request.steps)
            await session.flush()
        except IntegrityError as exc:
            raise BusinessRuleException(
                status_code=409,
                error_code="slug_taken",
                message="workflow slug already exists",
            ) from exc

        response = CreateWorkflowResponse(
            workflow=await workflow_from_db(session, db_workflow)
        )
        audit.target_id = db_workflow.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def update_workflow(
    session: AsyncSession,
    workflow_id: UUID,
    request: UpdateWorkflowRequest,
    *,
    actor_id: UUID,
) -> UpdateWorkflowResponse:
    response: UpdateWorkflowResponse | None = None
    request_payload = {
        "workflow_id": str(workflow_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=UPDATE_WORKFLOW_OP,
        target_kind=WORKFLOW_TARGET_KIND,
        target_id=workflow_id,
        request_payload=request_payload,
    ) as audit:
        old_workflow = await session.get(DbWorkflow, workflow_id)
        if old_workflow is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="workflow_not_found",
                message="workflow not found",
            )

        latest = await _latest_family_workflow(
            session,
            slug=old_workflow.slug,
            lock=True,
        )
        if latest is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="workflow_not_found",
                message="workflow not found",
            )
        if latest.is_archived:
            raise BusinessRuleException(
                status_code=409,
                error_code="workflow_archived",
                message="workflow family is archived",
            )
        if latest.id != old_workflow.id:
            raise BusinessRuleException(
                status_code=409,
                error_code="workflow_not_latest",
                message="workflow id is not the latest version",
            )

        _assert_unique_ordinals(request.steps)
        await _assert_contract_profiles_exist(session, request.steps)

        db_workflow = DbWorkflow(
            slug=old_workflow.slug,
            name=request.name,
            version=latest.version + 1,
            created_by_actor_id=actor_id,
            supersedes_workflow_id=old_workflow.id,
        )
        try:
            session.add(db_workflow)
            await session.flush()
            _add_steps(session, workflow_id=db_workflow.id, steps=request.steps)
            await session.flush()
        except IntegrityError as exc:
            raise BusinessRuleException(
                status_code=409,
                error_code="workflow_not_latest",
                message="workflow id is not the latest version",
            ) from exc

        response = UpdateWorkflowResponse(
            workflow=await workflow_from_db(session, db_workflow)
        )
        audit.target_id = db_workflow.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def archive_workflow(
    session: AsyncSession,
    slug: str,
) -> ArchiveWorkflowResponse:
    response: ArchiveWorkflowResponse | None = None
    async with audited_op(
        session,
        op=ARCHIVE_WORKFLOW_OP,
        target_kind=WORKFLOW_TARGET_KIND,
        request_payload={"slug": slug},
    ) as audit:
        family_rows = list(
            (
                await session.scalars(
                    select(DbWorkflow)
                    .where(DbWorkflow.slug == slug)
                    .order_by(DbWorkflow.version.asc())
                    .with_for_update()
                )
            ).all()
        )
        if not family_rows:
            raise BusinessRuleException(
                status_code=404,
                error_code="workflow_not_found",
                message="workflow not found",
            )

        latest = family_rows[-1]
        for workflow in family_rows:
            workflow.is_archived = True

        await session.flush()
        response = ArchiveWorkflowResponse(
            slug=slug,
            archived_count=len(family_rows),
        )
        audit.target_id = latest.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
