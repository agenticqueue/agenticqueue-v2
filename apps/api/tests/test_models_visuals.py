from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import CreateVisualRequest, UpdateVisualRequest, Visual
from aq_api.models.db import Visual as DbVisual
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
VISUAL_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = "2026-05-04T13:00:00Z"

PUBLIC_MODELS = (Visual, CreateVisualRequest, UpdateVisualRequest)


def _visual() -> Visual:
    return Visual(
        id=VISUAL_ID,
        attached_to_kind="decision",
        attached_to_id=PROJECT_ID,
        type="mermaid",
        spec="graph TD\n  A[Plan] --> B[Ship]",
        caption="Project decision flow",
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        deactivated_at=None,
    )


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def test_visual_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    visual = _visual()
    with pytest.raises(ValidationError):
        Visual.model_validate({**visual.model_dump(), "unexpected": "blocked"})
    with pytest.raises(ValidationError):
        visual.caption = "changed"  # type: ignore[misc]


def test_visual_models_round_trip_and_normalize_datetimes() -> None:
    visual = _visual()
    create_request = CreateVisualRequest(
        attached_to_kind="project",
        attached_to_id=PROJECT_ID,
        type="vega-lite",
        spec='{"mark":"bar","encoding":{}}',
        caption="Metrics chart",
    )
    update_request = UpdateVisualRequest(
        spec="digraph { a -> b }",
        caption=None,
    )

    for model in (visual, create_request, update_request):
        _round_trip(model)

    assert visual.created_at == datetime(2026, 5, 4, 13, tzinfo=UTC)


def test_visual_models_reject_invalid_shapes() -> None:
    with pytest.raises(ValidationError):
        CreateVisualRequest(
            attached_to_kind="component",
            attached_to_id=PROJECT_ID,
            type="mermaid",
            spec="graph TD",
        )
    with pytest.raises(ValidationError):
        CreateVisualRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            type="drawio",
            spec="graph TD",
        )
    with pytest.raises(ValidationError):
        CreateVisualRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            type="ascii",
            spec="",
        )
    with pytest.raises(ValidationError):
        CreateVisualRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            type="ascii",
            spec="x" * 65537,
        )
    with pytest.raises(ValidationError):
        CreateVisualRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            type="ascii",
            spec="ok",
            caption="x" * 513,
        )
    with pytest.raises(ValidationError):
        Visual(
            id=VISUAL_ID,
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            type="ascii",
            spec="ok",
            created_by_actor_id=ACTOR_ID,
            created_at=datetime(2026, 5, 4, 13),
        )
    with pytest.raises(ValidationError):
        UpdateVisualRequest(type="ascii")  # type: ignore[call-arg]


def test_visual_sqlalchemy_model_matches_migration_contract() -> None:
    table = DbVisual.__table__
    assert table.name == "visuals"
    assert list(table.columns.keys()) == [
        "id",
        "attached_to_kind",
        "attached_to_id",
        "type",
        "spec",
        "caption",
        "created_by_actor_id",
        "created_at",
        "deactivated_at",
    ]
    nullable = {column.name: column.nullable for column in table.columns}
    assert nullable == {
        "id": False,
        "attached_to_kind": False,
        "attached_to_id": False,
        "type": False,
        "spec": False,
        "caption": True,
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
        "visuals_attached_to_kind_check": (
            "attached_to_kind IN "
            "('project','pipeline','job','decision','learning')"
        ),
        "visuals_type_check": (
            "type IN ('mermaid','graphviz','plantuml','vega-lite','ascii')"
        ),
    }
    indexes = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert indexes == {
        "idx_visuals_actor": ("created_by_actor_id", "created_at"),
        "idx_visuals_attached": (
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        "idx_visuals_type": ("type",),
    }
