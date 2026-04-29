from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    AttachLabelRequest,
    AttachLabelResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    Label,
    RegisterLabelRequest,
    RegisterLabelResponse,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Label as DbLabel
from aq_api.models.db import Project as DbProject

REGISTER_LABEL_OP = "register_label"
ATTACH_LABEL_OP = "attach_label"
DETACH_LABEL_OP = "detach_label"
LABEL_TARGET_KIND = "label"
JOB_TARGET_KIND = "job"


def label_from_db(label: DbLabel) -> Label:
    return Label(
        id=label.id,
        project_id=label.project_id,
        name=label.name,
        color=label.color,
        created_at=label.created_at,
        archived_at=label.archived_at,
    )


async def _active_label_for_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    name: str,
) -> DbLabel | None:
    label: DbLabel | None = await session.scalar(
        select(DbLabel)
        .where(
            DbLabel.project_id == project_id,
            DbLabel.name == name,
            DbLabel.archived_at.is_(None),
        )
        .limit(1)
    )
    return label


async def register_label(
    session: AsyncSession,
    project_id: UUID,
    request: RegisterLabelRequest,
) -> RegisterLabelResponse:
    response: RegisterLabelResponse | None = None
    request_payload = {"project_id": str(project_id), **request.model_dump(mode="json")}
    async with audited_op(
        session,
        op=REGISTER_LABEL_OP,
        target_kind=LABEL_TARGET_KIND,
        request_payload=request_payload,
    ) as audit:
        project = await session.get(DbProject, project_id)
        if project is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="project_not_found",
                message="project not found",
            )

        existing = await _active_label_for_project(
            session,
            project_id=project_id,
            name=request.name,
        )
        if existing is not None:
            audit.target_id = existing.id
            raise BusinessRuleException(
                status_code=409,
                error_code="label_already_exists",
                message="label already exists",
            )

        db_label = DbLabel(
            project_id=project_id,
            name=request.name,
            color=request.color,
        )
        session.add(db_label)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise BusinessRuleException(
                status_code=409,
                error_code="label_already_exists",
                message="label already exists",
            ) from exc

        response = RegisterLabelResponse(label=label_from_db(db_label))
        audit.target_id = db_label.id
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def _locked_job(session: AsyncSession, job_id: UUID) -> DbJob | None:
    job: DbJob | None = await session.scalar(
        select(DbJob).where(DbJob.id == job_id).with_for_update().limit(1)
    )
    return job


async def attach_label(
    session: AsyncSession,
    job_id: UUID,
    request: AttachLabelRequest,
) -> AttachLabelResponse:
    response: AttachLabelResponse | None = None
    request_payload = {"job_id": str(job_id), **request.model_dump(mode="json")}
    async with audited_op(
        session,
        op=ATTACH_LABEL_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=request_payload,
    ) as audit:
        job = await _locked_job(session, job_id)
        if job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        label = await _active_label_for_project(
            session,
            project_id=job.project_id,
            name=request.label_name,
        )
        if label is None:
            raise BusinessRuleException(
                status_code=403,
                error_code="label_not_in_project",
                message="label not registered for job project",
            )

        labels = list(job.labels or [])
        if request.label_name not in labels:
            labels.append(request.label_name)
            job.labels = labels

        await session.flush()
        response = AttachLabelResponse(job_id=job.id, labels=labels)
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response


async def detach_label(
    session: AsyncSession,
    job_id: UUID,
    request: DetachLabelRequest,
) -> DetachLabelResponse:
    response: DetachLabelResponse | None = None
    request_payload = {"job_id": str(job_id), **request.model_dump(mode="json")}
    async with audited_op(
        session,
        op=DETACH_LABEL_OP,
        target_kind=JOB_TARGET_KIND,
        target_id=job_id,
        request_payload=request_payload,
    ) as audit:
        job = await _locked_job(session, job_id)
        if job is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="job_not_found",
                message="job not found",
            )

        current_labels = list(job.labels or [])
        labels = [label for label in current_labels if label != request.label_name]
        if labels != current_labels:
            job.labels = labels

        await session.flush()
        response = DetachLabelResponse(job_id=job.id, labels=labels)
        audit.response_payload = response.model_dump(mode="json")

    assert response is not None
    return response
