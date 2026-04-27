from contextvars import ContextVar, Token
from uuid import UUID

authenticated_actor_id: ContextVar[UUID | None] = ContextVar(
    "authenticated_actor_id",
    default=None,
)
claimed_actor_identity: ContextVar[str | None] = ContextVar(
    "claimed_actor_identity",
    default=None,
)


def set_authenticated_actor_id(actor_id: UUID) -> Token[UUID | None]:
    return authenticated_actor_id.set(actor_id)


def reset_authenticated_actor_id(token: Token[UUID | None]) -> None:
    authenticated_actor_id.reset(token)


def get_authenticated_actor_id() -> UUID | None:
    return authenticated_actor_id.get()


def set_claimed_actor_identity(identity: str | None) -> Token[str | None]:
    return claimed_actor_identity.set(identity)


def reset_claimed_actor_identity(token: Token[str | None]) -> None:
    claimed_actor_identity.reset(token)


def get_claimed_actor_identity() -> str | None:
    return claimed_actor_identity.get()
