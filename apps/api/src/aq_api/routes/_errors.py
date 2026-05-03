from fastapi.responses import JSONResponse

from aq_api._audit import BusinessRuleException


def business_rule_response(exc: BusinessRuleException) -> JSONResponse:
    payload: dict[str, object] = {"error": exc.error_code}
    if exc.details is not None:
        payload["details"] = exc.details
    return JSONResponse(payload, status_code=exc.status_code)
