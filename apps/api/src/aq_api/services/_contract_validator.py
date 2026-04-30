from aq_api._audit import BusinessRuleException
from aq_api.models import SubmitJobDoneRequest


def _contract_violation(rule: str, **details: object) -> BusinessRuleException:
    return BusinessRuleException(
        status_code=422,
        error_code="contract_violation",
        message=f"contract validation failed: {rule}",
        details={"rule": rule, **details},
    )


def _contract_dod_ids(contract: dict[str, object]) -> set[str]:
    dod_items = contract.get("dod_items")
    if not isinstance(dod_items, list):
        raise _contract_violation("missing_dod_items", field="contract.dod_items")

    dod_ids: set[str] = set()
    for index, item in enumerate(dod_items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _contract_violation(
                "invalid_dod_item",
                field=f"contract.dod_items[{index}].id",
            )
        dod_ids.add(item["id"])
    return dod_ids


def validate_done_submission(
    contract: dict[str, object],
    request: SubmitJobDoneRequest,
) -> None:
    contract_dod_ids = _contract_dod_ids(contract)
    seen: set[str] = set()

    for index, result in enumerate(request.dod_results):
        if result.dod_id in seen:
            raise _contract_violation(
                "duplicate_dod_id",
                field=f"dod_results[{index}].dod_id",
                dod_id=result.dod_id,
            )
        seen.add(result.dod_id)

        if result.dod_id not in contract_dod_ids:
            raise _contract_violation(
                "dod_id_unknown",
                field=f"dod_results[{index}].dod_id",
                dod_id=result.dod_id,
            )

    missing = sorted(contract_dod_ids - seen)
    if missing:
        raise _contract_violation(
            "missing_required_dod",
            field="dod_results",
            dod_id=missing[0],
            missing_dod_ids=missing,
        )

    for index, result in enumerate(request.dod_results):
        if result.status in {"failed", "blocked"}:
            raise _contract_violation(
                "incomplete_dod",
                field=f"dod_results[{index}].status",
                dod_id=result.dod_id,
                status=result.status,
            )

        if result.status == "passed" and not result.evidence:
            raise _contract_violation(
                "no_evidence",
                field=f"dod_results[{index}].evidence",
                dod_id=result.dod_id,
            )
