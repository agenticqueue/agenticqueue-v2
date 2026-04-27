from datetime import UTC, datetime
from uuid import UUID

import pytest
from aq_api.models import AuditLogEntry, AuditLogPage, AuditQueryParams
from pydantic import ValidationError

AUDIT_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("44444444-4444-4444-8444-444444444444")


def _audit_entry() -> AuditLogEntry:
    return AuditLogEntry(
        id=AUDIT_ID,
        ts="2026-04-27T01:00:00Z",
        op="create_actor",
        authenticated_actor_id=ACTOR_ID,
        claimed_actor_identity="codex",
        target_kind="actor",
        target_id=TARGET_ID,
        request_payload={"name": "worker"},
        response_payload={"status": "ok"},
    )


def test_audit_entry_normalizes_z_datetime() -> None:
    entry = _audit_entry()

    assert entry.ts == datetime(2026, 4, 27, 1, 0, tzinfo=UTC)


def test_audit_entry_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        AuditLogEntry(
            id=AUDIT_ID,
            ts=datetime(2026, 4, 27, 1, 0),
            op="create_actor",
            authenticated_actor_id=ACTOR_ID,
        )


def test_audit_query_rejects_naive_datetime_window() -> None:
    with pytest.raises(ValidationError):
        AuditQueryParams(since=datetime(2026, 4, 27, 1, 0))

    with pytest.raises(ValidationError):
        AuditQueryParams(until=datetime(2026, 4, 27, 1, 0))


def test_audit_query_limits_are_clamped_by_contract() -> None:
    assert AuditQueryParams().limit == 50
    assert AuditQueryParams(limit=200).limit == 200

    with pytest.raises(ValidationError):
        AuditQueryParams(limit=201)


def test_audit_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AuditLogEntry.model_validate(
            {
                "id": AUDIT_ID,
                "ts": "2026-04-27T01:00:00Z",
                "op": "create_actor",
                "authenticated_actor_id": ACTOR_ID,
                "plaintext_key": "must-not-cross-wire",
            }
        )


def test_audit_log_page_validates_entries() -> None:
    page = AuditLogPage(entries=[_audit_entry()], next_cursor=None)

    assert page.entries[0].op == "create_actor"
