from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import (
    Actor,
    ApiKey,
    CreateActorRequest,
    CreateActorResponse,
    RevokeApiKeyResponse,
    SetupRequest,
    SetupResponse,
    WhoamiResponse,
)
from pydantic import ValidationError

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
API_KEY_ID = UUID("22222222-2222-4222-8222-222222222222")
PLAINTEXT_KEY = "aq2_plaintext_contract_test_key"


def _actor() -> Actor:
    return Actor(
        id=ACTOR_ID,
        name="founder",
        kind="human",
        created_at="2026-04-27T01:00:00Z",
    )


def _api_key() -> ApiKey:
    return ApiKey(
        id=API_KEY_ID,
        actor_id=ACTOR_ID,
        name="default",
        prefix="aq2_test",
        created_at=datetime(2026, 4, 27, 1, 0, tzinfo=UTC),
    )


def test_actor_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        Actor(
            id=ACTOR_ID,
            name="bad",
            kind="god",
            created_at="2026-04-27T01:00:00Z",
        )


def test_auth_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ApiKey.model_validate(
            {
                "id": API_KEY_ID,
                "actor_id": ACTOR_ID,
                "name": "default",
                "prefix": "aq2_test",
                "created_at": "2026-04-27T01:00:00Z",
                "key_hash": "must-not-cross-wire",
            }
        )

    with pytest.raises(ValidationError):
        ApiKey.model_validate(
            {
                "id": API_KEY_ID,
                "actor_id": ACTOR_ID,
                "name": "default",
                "prefix": "aq2_test",
                "created_at": "2026-04-27T01:00:00Z",
                "key": "must-not-cross-wire",
            }
        )

    with pytest.raises(ValidationError):
        SetupRequest.model_validate({"unexpected": "field"})


def test_api_key_display_model_never_dumps_key_material() -> None:
    payload = _api_key().model_dump()

    assert "key_hash" not in payload
    assert "key" not in payload


def test_plaintext_key_fields_do_not_leak_in_repr() -> None:
    setup = SetupResponse(actor_id=ACTOR_ID, founder_key=PLAINTEXT_KEY)
    created = CreateActorResponse(
        actor=_actor(),
        api_key=_api_key(),
        key=PLAINTEXT_KEY,
    )

    assert PLAINTEXT_KEY not in repr(setup)
    assert PLAINTEXT_KEY not in repr(created)


def test_auth_models_normalize_utc_strings() -> None:
    actor = _actor()
    response = WhoamiResponse(actor=actor)

    assert response.actor.created_at == datetime(2026, 4, 27, 1, 0, tzinfo=UTC)


def test_auth_models_reject_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        Actor(
            id=ACTOR_ID,
            name="founder",
            kind="human",
            created_at=datetime(2026, 4, 27, 1, 0),
        )

    with pytest.raises(ValidationError):
        ApiKey(
            id=API_KEY_ID,
            actor_id=ACTOR_ID,
            name="default",
            prefix="aq2_test",
            created_at=datetime(2026, 4, 27, 1, 0),
        )


def test_nested_auth_responses_validate_contracts() -> None:
    actor = _actor()
    api_key = _api_key()

    CreateActorRequest(name="worker", kind="agent")
    RevokeApiKeyResponse(api_key=api_key)
    CreateActorResponse(actor=actor, api_key=api_key, key=PLAINTEXT_KEY)
