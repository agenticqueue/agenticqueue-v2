from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import Component, CreateComponentRequest, UpdateComponentRequest
from aq_api.models.db import Component as DbComponent
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
COMPONENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = "2026-05-04T13:00:00Z"

PUBLIC_MODELS = (Component, CreateComponentRequest, UpdateComponentRequest)


def _component() -> Component:
    return Component(
        id=COMPONENT_ID,
        attached_to_kind="project",
        attached_to_id=PROJECT_ID,
        name="Qdrant",
        purpose="Vector retrieval for Content Farm.",
        access_path="mcp__mmmmm-rag__search",
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        deactivated_at=None,
    )


def _round_trip(model: object) -> None:
    assert hasattr(model, "model_dump")
    model_type = type(model)
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    assert model_type.model_validate(payload) == model  # type: ignore[attr-defined]


def test_component_models_forbid_extra_fields_and_are_frozen() -> None:
    for model in PUBLIC_MODELS:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    component = _component()
    with pytest.raises(ValidationError):
        Component.model_validate({**component.model_dump(), "unexpected": "blocked"})
    with pytest.raises(ValidationError):
        component.name = "changed"  # type: ignore[misc]


def test_component_models_round_trip_and_normalize_datetimes() -> None:
    component = _component()
    create_request = CreateComponentRequest(
        attached_to_kind="pipeline",
        attached_to_id=PROJECT_ID,
        name="Cloudflare Worker",
        purpose="Publish generated pages.",
        access_path="https://api.cloudflare.com/client/v4/accounts",
    )
    update_request = UpdateComponentRequest(
        name="Worker",
        purpose=None,
        access_path="workers/content-farm",
    )

    for model in (component, create_request, update_request):
        _round_trip(model)

    assert component.created_at == datetime(2026, 5, 4, 13, tzinfo=UTC)


def test_component_models_reject_invalid_shapes() -> None:
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="job",
            attached_to_id=PROJECT_ID,
            name="Job-local tool",
            access_path="tool",
        )
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="",
            access_path="tool",
        )
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="x" * 257,
            access_path="tool",
        )
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="Valid",
            purpose="x" * 16385,
            access_path="tool",
        )
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="Valid",
            access_path="",
        )
    with pytest.raises(ValidationError):
        CreateComponentRequest(
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="Valid",
            access_path="x" * 1025,
        )
    with pytest.raises(ValidationError):
        Component(
            id=COMPONENT_ID,
            attached_to_kind="project",
            attached_to_id=PROJECT_ID,
            name="Qdrant",
            access_path="tool",
            created_by_actor_id=ACTOR_ID,
            created_at=datetime(2026, 5, 4, 13),
        )
    with pytest.raises(ValidationError):
        UpdateComponentRequest(attached_to_kind="project")  # type: ignore[call-arg]


def test_component_sqlalchemy_model_matches_migration_contract() -> None:
    table = DbComponent.__table__
    assert table.name == "components"
    assert list(table.columns.keys()) == [
        "id",
        "attached_to_kind",
        "attached_to_id",
        "name",
        "purpose",
        "access_path",
        "created_by_actor_id",
        "created_at",
        "deactivated_at",
    ]
    nullable = {column.name: column.nullable for column in table.columns}
    assert nullable == {
        "id": False,
        "attached_to_kind": False,
        "attached_to_id": False,
        "name": False,
        "purpose": True,
        "access_path": False,
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
        "components_attached_to_kind_check": (
            "attached_to_kind IN ('project','pipeline')"
        )
    }
    indexes = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert indexes == {
        "idx_components_actor": ("created_by_actor_id", "created_at"),
        "idx_components_attached": (
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
    }
