import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import aq_api.app as app_module
import aq_api.services.claim_auto_release as sweep_service
import httpx
import pytest
from aq_api._request_context import get_authenticated_actor_id

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live lifespan sweep tests",
)


@asynccontextmanager
async def _fake_mcp_lifespan(_app: object) -> AsyncIterator[None]:
    yield


@pytest.mark.asyncio
async def test_app_lifespan_starts_and_cancels_sweep_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_actor_id = uuid4()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    observed_actor_id: UUID | None = None

    async def fake_ensure_system_actor(_session: object) -> UUID:
        return system_actor_id

    async def fake_loop(initial_system_actor_id: UUID | None) -> None:
        nonlocal observed_actor_id
        observed_actor_id = initial_system_actor_id
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(app_module, "ensure_system_actor", fake_ensure_system_actor)
    monkeypatch.setattr(app_module, "claim_auto_release_loop", fake_loop)
    monkeypatch.setattr(app_module, "_mcp_lifespan", _fake_mcp_lifespan)

    async with app_module.app_lifespan(app_module.app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert observed_actor_id == system_actor_id

    assert cancelled.is_set()
    assert get_authenticated_actor_id() is None


@pytest.mark.asyncio
async def test_app_lifespan_tolerates_startup_system_actor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    observed_actor_id: UUID | None = uuid4()

    async def fake_ensure_system_actor(_session: object) -> UUID:
        raise RuntimeError("database temporarily unavailable")

    async def fake_loop(initial_system_actor_id: UUID | None) -> None:
        nonlocal observed_actor_id
        observed_actor_id = initial_system_actor_id
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(app_module, "ensure_system_actor", fake_ensure_system_actor)
    monkeypatch.setattr(app_module, "claim_auto_release_loop", fake_loop)
    monkeypatch.setattr(app_module, "_mcp_lifespan", _fake_mcp_lifespan)

    async with app_module.app_lifespan(app_module.app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert observed_actor_id is None
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 200

    assert cancelled.is_set()
    assert get_authenticated_actor_id() is None


@pytest.mark.asyncio
async def test_claim_auto_release_loop_retries_until_system_actor_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_actor_id = uuid4()
    ensure_calls = 0
    run_calls: list[UUID] = []
    sleep_calls = 0

    async def fake_ensure_system_actor(_session: object) -> UUID:
        nonlocal ensure_calls
        ensure_calls += 1
        if ensure_calls == 1:
            raise RuntimeError("system actor unavailable")
        return system_actor_id

    async def fake_run_once(
        _session: object,
        *,
        now: datetime,
        system_actor_id: UUID | None = None,
    ) -> int:
        assert now.tzinfo is UTC
        assert system_actor_id is not None
        run_calls.append(system_actor_id)
        return 0

    async def fake_sleep(_seconds: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(sweep_service, "ensure_system_actor", fake_ensure_system_actor)
    monkeypatch.setattr(
        sweep_service,
        "run_claim_auto_release_once",
        fake_run_once,
    )

    with pytest.raises(asyncio.CancelledError):
        await sweep_service.claim_auto_release_loop(
            None,
            sleep=fake_sleep,
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert ensure_calls == 2
    assert run_calls == [system_actor_id]


@pytest.mark.asyncio
async def test_claim_auto_release_loop_revalidates_cached_system_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_actor_id = uuid4()
    current_actor_id = uuid4()
    ensure_calls = 0
    run_calls: list[UUID] = []
    sleep_calls = 0

    async def fake_ensure_system_actor(_session: object) -> UUID:
        nonlocal ensure_calls
        ensure_calls += 1
        return current_actor_id

    async def fake_run_once(
        _session: object,
        *,
        now: datetime,
        system_actor_id: UUID | None = None,
    ) -> int:
        assert now.tzinfo is UTC
        assert system_actor_id is not None
        run_calls.append(system_actor_id)
        return 0

    async def fake_sleep(_seconds: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(sweep_service, "ensure_system_actor", fake_ensure_system_actor)
    monkeypatch.setattr(
        sweep_service,
        "run_claim_auto_release_once",
        fake_run_once,
    )

    with pytest.raises(asyncio.CancelledError):
        await sweep_service.claim_auto_release_loop(
            stale_actor_id,
            sleep=fake_sleep,
            now_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert ensure_calls == 1
    assert run_calls == [current_actor_id]
