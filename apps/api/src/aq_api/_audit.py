from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.services.audit import record


class BusinessRuleException(Exception):
    def __init__(self, *, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass
class AuditOperation:
    target_id: UUID | None = None
    response_payload: Mapping[str, object] | None = None
    # Cap #4 auto-release is a successful system mutation with a diagnostic code.
    error_code: str | None = None


@asynccontextmanager
async def audited_op(
    session: AsyncSession,
    *,
    op: str,
    target_kind: str | None = None,
    target_id: UUID | None = None,
    request_payload: Mapping[str, object] | None = None,
    skip_success_audit: bool = False,
) -> AsyncIterator[AuditOperation]:
    audit = AuditOperation(target_id=target_id)
    try:
        yield audit
    except BusinessRuleException as exc:
        await session.rollback()
        try:
            response_payload = audit.response_payload or {"error": exc.error_code}
            await record(
                session,
                op=op,
                target_kind=target_kind,
                target_id=audit.target_id,
                request_payload=request_payload,
                response_payload=response_payload,
                error_code=exc.error_code,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        raise
    except Exception:
        await session.rollback()
        raise
    else:
        try:
            if not skip_success_audit:
                await record(
                    session,
                    op=op,
                    target_kind=target_kind,
                    target_id=audit.target_id,
                    request_payload=request_payload,
                    response_payload=audit.response_payload,
                    error_code=audit.error_code,
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
