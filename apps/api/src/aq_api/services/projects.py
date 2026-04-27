import base64
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api._datetime import parse_utc
from aq_api.models import (
    ArchiveProjectResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    GetProjectResponse,
    ListProjectsResponse,
    Project,
    UpdateProjectRequest,
    UpdateProjectResponse,
)
from aq_api.models.db import Project as DbProject

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CREATE_PROJECT_OP = "create_project"
UPDATE_PROJECT_OP = "update_project"
ARCHIVE_PROJECT_OP = "archive_project"
PROJECT_TARGET_KIND = "project"


class InvalidProjectCursorError(Exception):
    pass


class ProjectNotFoundError(Exception):
    pass


def project_from_db(project: DbProject) -> Project:
    return Project(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        archived_at=project.archived_at,
        created_at=project.created_at,
        created_by_actor_id=project.created_by_actor_id,
    )


def encode_project_cursor(project: DbProject) -> str:
    payload = json.dumps(
        {
            "created_at": project.created_at.isoformat(),
            "id": str(project.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_project_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = parse_utc(str(payload["created_at"]))
        project_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidProjectCursorError("invalid project cursor") from exc
    return created_at, project_id


async def list_projects(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
    include_archived: bool = False,
) -> ListProjectsResponse:
    bounded_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    statement = select(DbProject)

    if not include_archived:
        statement = statement.where(DbProject.archived_at.is_(None))

    if cursor is not None:
        created_at, project_id = decode_project_cursor(cursor)
        statement = statement.where(
            or_(
                DbProject.created_at > created_at,
                and_(DbProject.created_at == created_at, DbProject.id > project_id),
            )
        )

    statement = statement.order_by(
        DbProject.created_at.asc(),
        DbProject.id.asc(),
    ).limit(bounded_limit + 1)
    rows = list((await session.scalars(statement)).all())
    page_rows = rows[:bounded_limit]
    next_cursor = (
        encode_project_cursor(page_rows[-1]) if len(rows) > bounded_limit else None
    )
    return ListProjectsResponse(
        projects=[project_from_db(project) for project in page_rows],
        next_cursor=next_cursor,
    )


async def get_project(
    session: AsyncSession,
    project_id: UUID,
) -> GetProjectResponse:
    project = await session.get(DbProject, project_id)
    if project is None:
        raise ProjectNotFoundError("project not found")
    return GetProjectResponse(project=project_from_db(project))


async def _project_id_by_slug(session: AsyncSession, slug: str) -> UUID | None:
    project_id: UUID | None = await session.scalar(
        select(DbProject.id).where(DbProject.slug == slug).limit(1)
    )
    return project_id


async def create_project(
    session: AsyncSession,
    request: CreateProjectRequest,
    *,
    actor_id: UUID,
) -> CreateProjectResponse:
    response: CreateProjectResponse | None = None
    async with audited_op(
        session,
        op=CREATE_PROJECT_OP,
        target_kind=PROJECT_TARGET_KIND,
        request_payload=request.model_dump(mode="json"),
    ) as audit:
        existing_id = await _project_id_by_slug(session, request.slug)
        if existing_id is not None:
            audit.target_id = existing_id
            raise BusinessRuleException(
                status_code=409,
                error_code="slug_taken",
                message="project slug already exists",
            )

        db_project = DbProject(
            name=request.name,
            slug=request.slug,
            description=request.description,
            created_by_actor_id=actor_id,
        )
        session.add(db_project)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise BusinessRuleException(
                status_code=409,
                error_code="slug_taken",
                message="project slug already exists",
            ) from exc

        response = CreateProjectResponse(project=project_from_db(db_project))
        audit.target_id = db_project.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def update_project(
    session: AsyncSession,
    project_id: UUID,
    request: UpdateProjectRequest,
) -> UpdateProjectResponse:
    response: UpdateProjectResponse | None = None
    request_payload = {
        "project_id": str(project_id),
        **request.model_dump(mode="json"),
    }
    async with audited_op(
        session,
        op=UPDATE_PROJECT_OP,
        target_kind=PROJECT_TARGET_KIND,
        target_id=project_id,
        request_payload=request_payload,
    ) as audit:
        db_project = await session.get(DbProject, project_id)
        if db_project is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="project_not_found",
                message="project not found",
            )

        if "name" in request.model_fields_set and request.name is not None:
            db_project.name = request.name
        if "description" in request.model_fields_set:
            db_project.description = request.description

        await session.flush()
        response = UpdateProjectResponse(project=project_from_db(db_project))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def archive_project(
    session: AsyncSession,
    project_id: UUID,
) -> ArchiveProjectResponse:
    response: ArchiveProjectResponse | None = None
    async with audited_op(
        session,
        op=ARCHIVE_PROJECT_OP,
        target_kind=PROJECT_TARGET_KIND,
        target_id=project_id,
        request_payload={"project_id": str(project_id)},
    ) as audit:
        db_project = await session.get(DbProject, project_id)
        if db_project is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="project_not_found",
                message="project not found",
            )

        if db_project.archived_at is None:
            db_project.archived_at = datetime.now(UTC)

        await session.flush()
        response = ArchiveProjectResponse(project=project_from_db(db_project))
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
