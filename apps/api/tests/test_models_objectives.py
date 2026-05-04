from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import CreateObjectiveRequest, Objective, UpdateObjectiveRequest
from aq_api.models.db import Objective as DbObjective
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Index

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
OBJECTIVE_ID = UUID("88888888-8888-4888-8888-888888888888")
NOW = "2026-05-04T13:00:00Z"
DUE_AT = "2026-06-04T13:00:00Z"

PUBLIC_MODELS = (Objective, CreateObjectiveRequest, UpdateObjectiveRequest)


def _objective() -> Objective:
    return Objective(
        id=OBJECTIVE_ID,
        attached_to_kind="project",
        attached_to_id=PROJECT_ID,
        statement="Ship Content Farm with structured Project context.",
        metric="project-context-coverage",
        target_value="100%",
        due_at=DUE_AT,
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        deactivated_at=None,
    )


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def test_objective_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    objective = _objective()
    with pytest.raises(ValidationError):
        Objective.model_validate({**objective.model_dump(), "unexpected": "blocked"})
    with pytest.raises(ValidationError):
        objective.statement = "changed"  # type: ignore[misc]


def test_objective_models_round_trip_and_normalize_datetimes() -> None:
    objective = _objective()
    create_request = CreateObjectiveRequest(
        attached_to_kind="pipeline",
        attached_to_id=PROJECT_ID,
        statement="Keep downstream agents aligned.",
        metric="context-misses",
        target_value="0",
        due_at=DUE_AT,
    )
    update_request = UpdateObjectiveRequest(
        statement="Updated goal",
        metric=None,
        target_value="done",
        due_at=None,
    )

    for model in (objective, create_request, update_request):
        _round_trip(model)

    assert objective.created_at == datetime(2026, 5, 4, 13, tzinfo=UTC)
    assert objective.due_at == datetime(2026, 6, 4, 13, tzinfo=UTC)


def test_objective_models_reject_invalid_shapes() -> None:
    with pytest.raises(ValidationError):
        CreateObjectiveRequest(
            attached_to_kind="job",
            attached_to_id=PROJECT_ID,
            statement="Objectives cannot attach to jobs.",
        )
    with pytest.raises(ValidationError):
        CreateObjectiveRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            statement="",
        )
    with pytest.raises(ValidationError):
        CreateObjectiveRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            statement="x" * 16385,
        )
    with pytest.raises(ValidationError):
        CreateObjectiveRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            statement="Valid",
            metric="x" * 513,
        )
    with pytest.raises(ValidationError):
        Objective(
            id=OBJECTIVE_ID,
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            statement="Naive due date is invalid.",
            created_by_actor_id=ACTOR_ID,
            created_at=NOW,
            due_at=datetime(2026, 6, 4, 13),
        )
    with pytest.raises(ValidationError):
        UpdateObjectiveRequest(attached_to_kind="project")  # type: ignore[call-arg]


def test_objective_sqlalchemy_model_matches_migration_contract() -> None:
    table = DbObjective.__table__
    assert table.name == "objectives"
    assert list(table.columns.keys()) == [
        "id",
        "attached_to_kind",
        "attached_to_id",
        "statement",
        "metric",
        "target_value",
        "due_at",
        "created_by_actor_id",
        "created_at",
        "deactivated_at",
    ]
    nullable = {column.name: column.nullable for column in table.columns}
    assert nullable == {
        "id": False,
        "attached_to_kind": False,
        "attached_to_id": False,
        "statement": False,
        "metric": True,
        "target_value": True,
        "due_at": True,
        "created_by_actor_id": False,
        "created_at": False,
        "deactivated_at": True,
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "objectives_attached_to_kind_check": (
            "attached_to_kind IN ('project','pipeline')"
        )
    }
    indexes = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert indexes == {
        "idx_objectives_actor": ("created_by_actor_id", "created_at"),
        "idx_objectives_attached": (
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
    }
    assert all(isinstance(index, Index) for index in table.indexes)
