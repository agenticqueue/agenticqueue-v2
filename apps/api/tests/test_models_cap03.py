from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import (
    ArchiveProjectResponse,
    ArchiveWorkflowResponse,
    AttachLabelRequest,
    AttachLabelResponse,
    CancelJobResponse,
    CommentOnJobRequest,
    CommentOnJobResponse,
    ContractProfile,
    CreateJobRequest,
    CreateJobResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    DescribeContractProfileResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    GetJobResponse,
    GetPipelineResponse,
    GetProjectResponse,
    GetWorkflowResponse,
    InstantiatePipelineRequest,
    InstantiatePipelineResponse,
    Job,
    JobComment,
    JobEdge,
    Label,
    ListContractProfilesResponse,
    ListJobCommentsRequest,
    ListJobCommentsResponse,
    ListJobsRequest,
    ListJobsResponse,
    ListPipelinesResponse,
    ListProjectsResponse,
    ListReadyJobsRequest,
    ListReadyJobsResponse,
    ListWorkflowsResponse,
    Pipeline,
    Project,
    RegisterLabelRequest,
    RegisterLabelResponse,
    UpdateJobRequest,
    UpdateJobResponse,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
    UpdateWorkflowRequest,
    UpdateWorkflowResponse,
    Workflow,
    WorkflowStep,
    WorkflowStepInput,
)
from pydantic import ValidationError

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
LABEL_ID = UUID("33333333-3333-4333-8333-333333333333")
WORKFLOW_ID = UUID("44444444-4444-4444-8444-444444444444")
STEP_ID = UUID("55555555-5555-4555-8555-555555555555")
PIPELINE_ID = UUID("66666666-6666-4666-8666-666666666666")
JOB_ID = UUID("77777777-7777-4777-8777-777777777777")
PROFILE_ID = UUID("88888888-8888-4888-8888-888888888888")
COMMENT_ID = UUID("99999999-9999-4999-8999-999999999999")
SECOND_JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = "2026-04-27T16:00:00Z"


PUBLIC_MODELS = (
    Project,
    CreateProjectRequest,
    CreateProjectResponse,
    ListProjectsResponse,
    GetProjectResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
    ArchiveProjectResponse,
    Label,
    RegisterLabelRequest,
    RegisterLabelResponse,
    AttachLabelRequest,
    AttachLabelResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    WorkflowStepInput,
    WorkflowStep,
    Workflow,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    ListWorkflowsResponse,
    GetWorkflowResponse,
    UpdateWorkflowRequest,
    UpdateWorkflowResponse,
    ArchiveWorkflowResponse,
    Pipeline,
    CreatePipelineRequest,
    CreatePipelineResponse,
    ListPipelinesResponse,
    GetPipelineResponse,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
    InstantiatePipelineRequest,
    InstantiatePipelineResponse,
    Job,
    JobEdge,
    CreateJobRequest,
    CreateJobResponse,
    ListJobsRequest,
    ListJobsResponse,
    GetJobResponse,
    UpdateJobRequest,
    UpdateJobResponse,
    ListReadyJobsRequest,
    ListReadyJobsResponse,
    JobComment,
    CommentOnJobRequest,
    CommentOnJobResponse,
    ListJobCommentsRequest,
    ListJobCommentsResponse,
    CancelJobResponse,
    ContractProfile,
    ListContractProfilesResponse,
    DescribeContractProfileResponse,
)


def _project() -> Project:
    return Project(
        id=PROJECT_ID,
        name="AQ 2.0 Backlog",
        slug="aq2-backlog",
        description="Work container",
        archived_at=None,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
    )


def _label() -> Label:
    return Label(
        id=LABEL_ID,
        project_id=PROJECT_ID,
        name="area:web",
        color="#3366ff",
        created_at=NOW,
        archived_at=None,
    )


def _profile() -> ContractProfile:
    return ContractProfile(
        id=PROFILE_ID,
        name="coding-task",
        version=1,
        description="Coding work",
        required_dod_ids=["DOD-CODE-01"],
        schema={"type": "object"},
    )


def _workflow_step_input() -> WorkflowStepInput:
    return WorkflowStepInput(
        name="build",
        ordinal=2,
        default_contract_profile_id=PROFILE_ID,
        step_edges={},
    )


def _workflow_step() -> WorkflowStep:
    return WorkflowStep(
        id=STEP_ID,
        workflow_id=WORKFLOW_ID,
        name="build",
        ordinal=2,
        default_contract_profile_id=PROFILE_ID,
        step_edges={},
    )


def _workflow() -> Workflow:
    return Workflow(
        id=WORKFLOW_ID,
        slug="ship-a-thing",
        name="Ship A Thing",
        version=1,
        is_archived=False,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
        supersedes_workflow_id=None,
        steps=[_workflow_step()],
    )


def _pipeline() -> Pipeline:
    return Pipeline(
        id=PIPELINE_ID,
        project_id=PROJECT_ID,
        name="Release train",
        instantiated_from_workflow_id=WORKFLOW_ID,
        instantiated_from_workflow_version=1,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
    )


def _job() -> Job:
    return Job(
        id=JOB_ID,
        pipeline_id=PIPELINE_ID,
        project_id=PROJECT_ID,
        state="ready",
        title="Build the thing",
        description="Implement the scoped change",
        contract_profile_id=PROFILE_ID,
        instantiated_from_step_id=STEP_ID,
        labels=["area:web"],
        claimed_by_actor_id=None,
        claimed_at=None,
        claim_heartbeat_at=None,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
    )


def _comment() -> JobComment:
    return JobComment(
        id=COMMENT_ID,
        job_id=JOB_ID,
        author_actor_id=ACTOR_ID,
        body="Looks ready.",
        created_at=NOW,
    )


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def test_cap03_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    with pytest.raises(ValidationError):
        Project.model_validate(
            {
                "id": PROJECT_ID,
                "name": "AQ 2.0",
                "slug": "aq2",
                "created_at": NOW,
                "unexpected": "blocked",
            }
        )

    project = _project()
    with pytest.raises(ValidationError):
        project.name = "mutated"  # type: ignore[misc]


def test_cap03_entity_models_round_trip_and_normalize_utc() -> None:
    entities = [
        _project(),
        _label(),
        _workflow_step(),
        _workflow(),
        _pipeline(),
        _job(),
        JobEdge(
            from_job_id=JOB_ID,
            to_job_id=SECOND_JOB_ID,
            edge_type="gated_on",
        ),
        _comment(),
        _profile(),
    ]

    for entity in entities:
        _round_trip(entity)

    assert _project().created_at == datetime(2026, 4, 27, 16, 0, tzinfo=UTC)


def test_cap03_request_and_response_models_round_trip() -> None:
    request_response_models = [
        CreateProjectRequest(
            name="AQ 2.0 Backlog",
            slug="aq2-backlog",
            description="Work container",
        ),
        CreateProjectResponse(project=_project()),
        ListProjectsResponse(projects=[_project()], next_cursor=None),
        GetProjectResponse(project=_project()),
        UpdateProjectRequest(name="AQ 2.0", description=None),
        UpdateProjectResponse(project=_project()),
        ArchiveProjectResponse(project=_project()),
        RegisterLabelRequest(name="area:web", color="#3366ff"),
        RegisterLabelResponse(label=_label()),
        AttachLabelRequest(label_name="area:web"),
        AttachLabelResponse(job_id=JOB_ID, labels=["area:web"]),
        DetachLabelRequest(label_name="area:web"),
        DetachLabelResponse(job_id=JOB_ID, labels=[]),
        CreateWorkflowRequest(
            slug="ship-a-thing",
            name="Ship A Thing",
            steps=[_workflow_step_input()],
        ),
        CreateWorkflowResponse(workflow=_workflow()),
        ListWorkflowsResponse(workflows=[_workflow()], next_cursor=None),
        GetWorkflowResponse(workflow=_workflow()),
        UpdateWorkflowRequest(
            name="Ship A Better Thing",
            steps=[_workflow_step_input()],
        ),
        UpdateWorkflowResponse(workflow=_workflow()),
        ArchiveWorkflowResponse(slug="ship-a-thing", archived_count=2),
        CreatePipelineRequest(project_id=PROJECT_ID, name="Release train"),
        CreatePipelineResponse(pipeline=_pipeline()),
        ListPipelinesResponse(pipelines=[_pipeline()], next_cursor=None),
        GetPipelineResponse(pipeline=_pipeline()),
        UpdatePipelineRequest(name="Release train 2"),
        UpdatePipelineResponse(pipeline=_pipeline()),
        InstantiatePipelineRequest(
            project_id=PROJECT_ID,
            pipeline_name="Release train",
        ),
        InstantiatePipelineResponse(pipeline=_pipeline(), jobs=[_job()]),
        CreateJobRequest(
            pipeline_id=PIPELINE_ID,
            title="Build the thing",
            description="Implement it",
            contract_profile_id=PROFILE_ID,
        ),
        CreateJobResponse(job=_job()),
        ListJobsRequest(project_id=PROJECT_ID, pipeline_id=PIPELINE_ID, state="ready"),
        ListJobsResponse(jobs=[_job()], next_cursor=None),
        GetJobResponse(job=_job()),
        UpdateJobRequest(title="Build it", description=None),
        UpdateJobResponse(job=_job()),
        ListReadyJobsRequest(project_id=PROJECT_ID, label_filter=["area:web"]),
        ListReadyJobsResponse(jobs=[_job()], next_cursor=None),
        CommentOnJobRequest(body="Looks ready."),
        CommentOnJobResponse(comment=_comment()),
        ListJobCommentsRequest(limit=50, cursor=None),
        ListJobCommentsResponse(comments=[_comment()], next_cursor=None),
        CancelJobResponse(job=_job()),
        ListContractProfilesResponse(profiles=[_profile()]),
        DescribeContractProfileResponse(profile=_profile()),
    ]

    for model in request_response_models:
        _round_trip(model)


def test_cap03_models_reject_malicious_slugs_and_names() -> None:
    bad_slugs = ["../escape", "BadSlug", "x" * 64, "ship_a_thing", "-bad"]
    for slug in bad_slugs:
        with pytest.raises(ValidationError):
            CreateProjectRequest(name="AQ", slug=slug)
        with pytest.raises(ValidationError):
            CreateWorkflowRequest(
                slug=slug,
                name="Workflow",
                steps=[_workflow_step_input()],
            )

    bad_label_names = ["area web", "../area:web", "area:web;DROP", "", "x" * 129]
    for name in bad_label_names:
        with pytest.raises(ValidationError):
            RegisterLabelRequest(name=name)
        with pytest.raises(ValidationError):
            AttachLabelRequest(label_name=name)

    with pytest.raises(ValidationError):
        CreatePipelineRequest(project_id=PROJECT_ID, name="")

    with pytest.raises(ValidationError):
        CommentOnJobRequest(body="")

    with pytest.raises(ValidationError):
        Job(
            **{
                **_job().model_dump(),
                "state": "claimed",
            }
        )


def test_cap03_models_reject_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        Project(
            id=PROJECT_ID,
            name="AQ 2.0",
            slug="aq2",
            created_at=datetime(2026, 4, 27, 16, 0),
        )
