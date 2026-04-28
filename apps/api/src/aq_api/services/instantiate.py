from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api._audit import BusinessRuleException, audited_op
from aq_api.models import (
    InstantiatePipelineRequest,
    InstantiatePipelineResponse,
    Job,
    JobState,
)
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject
from aq_api.models.db import Workflow as DbWorkflow
from aq_api.models.db import WorkflowStep as DbWorkflowStep
from aq_api.services.pipelines import PIPELINE_TARGET_KIND, pipeline_from_db

INSTANTIATE_PIPELINE_OP = "instantiate_pipeline"


def job_from_db(job: DbJob) -> Job:
    return Job(
        id=job.id,
        pipeline_id=job.pipeline_id,
        project_id=job.project_id,
        state=cast(JobState, job.state),
        title=job.title,
        description=job.description,
        contract_profile_id=job.contract_profile_id,
        instantiated_from_step_id=job.instantiated_from_step_id,
        labels=list(job.labels or []),
        claimed_by_actor_id=job.claimed_by_actor_id,
        claimed_at=job.claimed_at,
        claim_heartbeat_at=job.claim_heartbeat_at,
        created_at=job.created_at,
        created_by_actor_id=job.created_by_actor_id,
    )


async def _latest_family_workflow(
    session: AsyncSession,
    *,
    slug: str,
) -> DbWorkflow | None:
    return cast(
        DbWorkflow | None,
        await session.scalar(
            select(DbWorkflow)
            .where(DbWorkflow.slug == slug)
            .order_by(DbWorkflow.version.desc(), DbWorkflow.id.desc())
            .limit(1)
        ),
    )


async def _workflow_steps(
    session: AsyncSession,
    *,
    workflow_id: UUID,
) -> list[DbWorkflowStep]:
    return list(
        (
            await session.scalars(
                select(DbWorkflowStep)
                .where(DbWorkflowStep.workflow_id == workflow_id)
                .order_by(DbWorkflowStep.ordinal.asc(), DbWorkflowStep.id.asc())
            )
        ).all()
    )


async def _create_jobs_from_steps(
    session: AsyncSession,
    *,
    pipeline_id: UUID,
    project_id: UUID,
    actor_id: UUID,
    workflow_steps: list[DbWorkflowStep],
) -> list[DbJob]:
    jobs: list[DbJob] = []
    for step in workflow_steps:
        job = DbJob(
            pipeline_id=pipeline_id,
            project_id=project_id,
            state="ready",
            title=step.name,
            description=None,
            contract_profile_id=step.default_contract_profile_id,
            instantiated_from_step_id=step.id,
            created_by_actor_id=actor_id,
        )
        session.add(job)
        jobs.append(job)
    await session.flush()
    return jobs


async def instantiate_pipeline(
    session: AsyncSession,
    workflow_slug: str,
    request: InstantiatePipelineRequest,
    *,
    actor_id: UUID,
) -> InstantiatePipelineResponse:
    response: InstantiatePipelineResponse | None = None
    request_payload: dict[str, object] = {
        "workflow_slug": workflow_slug,
        "workflow_version": None,
        "project_id": str(request.project_id),
        "pipeline_name": request.pipeline_name,
    }
    async with audited_op(
        session,
        op=INSTANTIATE_PIPELINE_OP,
        target_kind=PIPELINE_TARGET_KIND,
        request_payload=request_payload,
    ) as audit:
        project = await session.get(DbProject, request.project_id)
        if project is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="project_not_found",
                message="project not found",
            )

        workflow = await _latest_family_workflow(session, slug=workflow_slug)
        if workflow is None:
            raise BusinessRuleException(
                status_code=404,
                error_code="workflow_not_found",
                message="workflow not found",
            )

        request_payload["workflow_version"] = workflow.version
        if workflow.is_archived:
            raise BusinessRuleException(
                status_code=409,
                error_code="workflow_archived",
                message="workflow family is archived",
            )

        steps = await _workflow_steps(session, workflow_id=workflow.id)
        pipeline = DbPipeline(
            project_id=request.project_id,
            name=request.pipeline_name,
            instantiated_from_workflow_id=workflow.id,
            instantiated_from_workflow_version=workflow.version,
            created_by_actor_id=actor_id,
        )
        session.add(pipeline)
        await session.flush()

        jobs = await _create_jobs_from_steps(
            session,
            pipeline_id=pipeline.id,
            project_id=request.project_id,
            actor_id=actor_id,
            workflow_steps=steps,
        )
        response = InstantiatePipelineResponse(
            pipeline=pipeline_from_db(pipeline),
            jobs=[job_from_db(job) for job in jobs],
        )
        audit.target_id = pipeline.id
        audit.response_payload = {
            "pipeline_id": str(pipeline.id),
            "job_count": len(jobs),
            "job_ids": [str(job.id) for job in jobs],
        }

    assert response is not None
    return response
