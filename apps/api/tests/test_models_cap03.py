from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import (
    ArchivePipelineResponse,
    ArchiveProjectResponse,
    AttachLabelRequest,
    AttachLabelResponse,
    CancelJobResponse,
    ClonePipelineRequest,
    ClonePipelineResponse,
    CommentOnJobRequest,
    CommentOnJobResponse,
    CreateJobRequest,
    CreateJobResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    DetachLabelRequest,
    DetachLabelResponse,
    GetJobResponse,
    GetPipelineResponse,
    GetProjectResponse,
    InheritanceReferenceLists,
    Job,
    JobComment,
    JobEdge,
    Label,
    ListJobCommentsRequest,
    ListJobCommentsResponse,
    ListJobsRequest,
    ListJobsResponse,
    ListPipelinesResponse,
    ListProjectsResponse,
    ListReadyJobsRequest,
    ListReadyJobsResponse,
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
)
from pydantic import ValidationError

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
LABEL_ID = UUID("33333333-3333-4333-8333-333333333333")
PIPELINE_ID = UUID("66666666-6666-4666-8666-666666666666")
JOB_ID = UUID("77777777-7777-4777-8777-777777777777")
COMMENT_ID = UUID("99999999-9999-4999-8999-999999999999")
SECOND_JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = "2026-04-27T16:00:00Z"
CONTRACT = {
    "contract_type": "coding-task",
    "dod_items": [{"id": "tests-pass"}],
}


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
    Pipeline,
    InheritanceReferenceLists,
    CreatePipelineRequest,
    CreatePipelineResponse,
    ListPipelinesResponse,
    GetPipelineResponse,
    UpdatePipelineRequest,
    UpdatePipelineResponse,
    ClonePipelineRequest,
    ClonePipelineResponse,
    ArchivePipelineResponse,
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


def _pipeline() -> Pipeline:
    return Pipeline(
        id=PIPELINE_ID,
        project_id=PROJECT_ID,
        name="Release train",
        is_template=False,
        cloned_from_pipeline_id=None,
        archived_at=None,
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
        contract=CONTRACT,
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
        _pipeline(),
        _job(),
        JobEdge(
            from_job_id=JOB_ID,
            to_job_id=SECOND_JOB_ID,
            edge_type="gated_on",
        ),
        _comment(),
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
        CreatePipelineRequest(project_id=PROJECT_ID, name="Release train"),
        CreatePipelineResponse(pipeline=_pipeline()),
        ListPipelinesResponse(pipelines=[_pipeline()], next_cursor=None),
        GetPipelineResponse(pipeline=_pipeline()),
        UpdatePipelineRequest(name="Release train 2"),
        UpdatePipelineResponse(pipeline=_pipeline()),
        ClonePipelineRequest(name="Customer ship"),
        ClonePipelineResponse(pipeline=_pipeline(), jobs=[_job()]),
        ArchivePipelineResponse(pipeline=_pipeline()),
        CreateJobRequest(
            pipeline_id=PIPELINE_ID,
            title="Build the thing",
            description="Implement it",
            contract=CONTRACT,
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
    ]

    for model in request_response_models:
        _round_trip(model)


def test_cap03_models_reject_malicious_slugs_and_names() -> None:
    bad_slugs = ["../escape", "BadSlug", "x" * 64, "ship_a_thing", "-bad"]
    for slug in bad_slugs:
        with pytest.raises(ValidationError):
            CreateProjectRequest(name="AQ", slug=slug)

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
