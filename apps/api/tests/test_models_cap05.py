from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from aq_api.models import (
    Decision,
    Learning,
    ReviewCompleteRequest,
    ReviewCompleteResponse,
    SubmitDecisionInline,
    SubmitJobBlockedRequest,
    SubmitJobDodResult,
    SubmitJobDoneRequest,
    SubmitJobFailedRequest,
    SubmitJobPendingReviewRequest,
    SubmitJobRequest,
    SubmitJobResponse,
    SubmitLearningInline,
)
from aq_api.models.jobs import Job
from pydantic import TypeAdapter, ValidationError

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
PIPELINE_ID = UUID("33333333-3333-4333-8333-333333333333")
JOB_ID = UUID("44444444-4444-4444-8444-444444444444")
GATED_JOB_ID = UUID("55555555-5555-4555-8555-555555555555")
DECISION_ID = UUID("66666666-6666-4666-8666-666666666666")
LEARNING_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = "2026-04-29T21:00:00Z"
CONTRACT = {"contract_type": "coding-task", "dod_items": [{"id": "tests-pass"}]}

PUBLIC_MODELS = (
    SubmitDecisionInline,
    Decision,
    SubmitLearningInline,
    Learning,
    SubmitJobDodResult,
    SubmitJobDoneRequest,
    SubmitJobPendingReviewRequest,
    SubmitJobFailedRequest,
    SubmitJobBlockedRequest,
    SubmitJobResponse,
    ReviewCompleteRequest,
    ReviewCompleteResponse,
)


def _job() -> Job:
    return Job(
        id=JOB_ID,
        pipeline_id=PIPELINE_ID,
        project_id=PROJECT_ID,
        state="done",
        title="Build the thing",
        description="Implement the scoped change",
        contract=CONTRACT,
        labels=[],
        claimed_by_actor_id=None,
        claimed_at=None,
        claim_heartbeat_at=None,
        created_at=NOW,
        created_by_actor_id=ACTOR_ID,
    )


def _dod_result(status: str = "passed") -> dict[str, object]:
    return {
        "dod_id": "tests-pass",
        "status": status,
        "evidence": ["artifacts/cap-05/test-output.txt"],
        "summary": "pytest passed",
    }


def _base_payload() -> dict[str, object]:
    return {
        "files_changed": ["apps/api/src/aq_api/models/jobs.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-S5.2",
        "learnings": [
            {
                "title": "Model shape",
                "statement": "Submit models round-trip through Pydantic.",
                "context": "Story 5.1",
            }
        ],
        "decisions_made": [
            {
                "title": "No audit row id",
                "statement": "SubmitJobResponse omits audit_row_id.",
                "rationale": "audited_op writes after service exit",
            }
        ],
    }


def _proof_payload() -> dict[str, object]:
    return {
        **_base_payload(),
        "dod_results": [_dod_result()],
        "commands_run": ["pytest -q apps/api/tests/test_models_cap05.py"],
        "verification_summary": "model tests passed",
    }


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def test_cap05_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    decision = SubmitDecisionInline(
        title="Decision",
        statement="Use the discriminated union.",
    )
    with pytest.raises(ValidationError):
        SubmitDecisionInline.model_validate(
            {**decision.model_dump(), "unexpected": "blocked"}
        )
    with pytest.raises(ValidationError):
        decision.title = "changed"  # type: ignore[misc]


def test_decision_and_learning_models_round_trip_and_normalize_utc() -> None:
    decision = Decision(
        id=DECISION_ID,
        attached_to_kind="job",
        attached_to_id=JOB_ID,
        title="Decision",
        statement="Keep cap-5 response lookup through cap-7.",
        rationale="audit rows are written after service exit",
        supersedes_decision_id=None,
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        deactivated_at=None,
    )
    learning = Learning(
        id=LEARNING_ID,
        attached_to_kind="project",
        attached_to_id=PROJECT_ID,
        title="Learning",
        statement="Attachment kind is constrained.",
        context="cap-9 forward-compatible shape",
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        deactivated_at=None,
    )

    _round_trip(decision)
    _round_trip(learning)
    assert decision.created_at == datetime(2026, 4, 29, 21, tzinfo=UTC)
    assert learning.created_at == datetime(2026, 4, 29, 21, tzinfo=UTC)

    with pytest.raises(ValidationError):
        Decision(
            id=DECISION_ID,
            attached_to_kind="global",
            attached_to_id=JOB_ID,
            title="Decision",
            statement="Bad attachment kind.",
            created_by_actor_id=ACTOR_ID,
            created_at=NOW,
        )
    with pytest.raises(ValidationError):
        Learning(
            id=LEARNING_ID,
            attached_to_kind="job",
            attached_to_id=JOB_ID,
            title="Learning",
            statement="Naive datetime rejected.",
            created_by_actor_id=ACTOR_ID,
            created_at=datetime(2026, 4, 29, 21),
        )


def test_submit_job_request_discriminator_selects_each_variant() -> None:
    adapter = TypeAdapter(SubmitJobRequest)
    cases: list[tuple[str, type[object], dict[str, object]]] = [
        (
            "done",
            SubmitJobDoneRequest,
            {
                **_proof_payload(),
                "outcome": "done",
            },
        ),
        (
            "pending_review",
            SubmitJobPendingReviewRequest,
            {
                **_proof_payload(),
                "outcome": "pending_review",
                "submitted_for_review": "needs human review",
            },
        ),
        (
            "failed",
            SubmitJobFailedRequest,
            {
                **_base_payload(),
                "outcome": "failed",
                "failure_reason": "upstream dependency failed",
                "dod_results": [],
                "commands_run": [],
                "verification_summary": "",
            },
        ),
        (
            "blocked",
            SubmitJobBlockedRequest,
            {
                **_base_payload(),
                "outcome": "blocked",
                "gated_on_job_id": str(GATED_JOB_ID),
                "blocker_reason": "waiting on prerequisite job",
            },
        ),
    ]

    for outcome, expected_type, payload in cases:
        parsed = adapter.validate_python(payload)
        assert isinstance(parsed, expected_type)
        assert parsed.outcome == outcome

    with pytest.raises(ValidationError):
        adapter.validate_python({**_base_payload(), "outcome": "paused"})


def test_submit_job_shapes_reject_invalid_or_out_of_scope_fields() -> None:
    adapter = TypeAdapter(SubmitJobRequest)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                **_proof_payload(),
                "outcome": "done",
                "audit_row_id": str(DECISION_ID),
            }
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                **_base_payload(),
                "outcome": "blocked",
                "gated_on_job_id": str(GATED_JOB_ID),
                "blocker_reason": "blocked",
                "dod_results": [_dod_result()],
            }
        )
    with pytest.raises(ValidationError):
        SubmitJobDodResult(
            dod_id="tests-pass",
            status=cast(object, "maybe"),
            evidence=[],
            summary="bad status",
        )


def test_submit_and_review_response_models_round_trip() -> None:
    submit_response = SubmitJobResponse(
        job=_job(),
        created_decisions=[DECISION_ID],
        created_learnings=[LEARNING_ID],
        created_gated_on_edge=False,
    )
    review_request = ReviewCompleteRequest(
        final_outcome="done",
        notes="approved",
    )
    review_response = ReviewCompleteResponse(job=_job())

    _round_trip(submit_response)
    _round_trip(review_request)
    _round_trip(review_response)

    assert "audit_row_id" not in SubmitJobResponse.model_fields
    with pytest.raises(ValidationError):
        ReviewCompleteRequest(final_outcome="blocked")
