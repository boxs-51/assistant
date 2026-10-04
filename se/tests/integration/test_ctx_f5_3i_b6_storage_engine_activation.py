from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from se.src.infrastructure.storage.core.manager import (
    StorageEngine,
    StorageServiceGenerationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
)


SERVICE_KEY = "tool_response_payload_memory_promotion"


class _FakeSQLiteDriver:
    def __init__(self) -> None:
        self.session_entries = 0

    @asynccontextmanager
    async def get_session(self):
        self.session_entries += 1
        yield object()


class _FakeDrivers:
    def __init__(self, sqlite_driver=None) -> None:
        self.sqlite_driver = sqlite_driver
        self.sqlite_available = False
        self.on_disconnect = None

    async def connect_all(self) -> None:
        self.sqlite_available = self.sqlite_driver is not None

    async def disconnect_all(self) -> None:
        if self.on_disconnect is not None:
            self.on_disconnect()
        self.sqlite_available = False

    def is_available(self, name: str) -> bool:
        return name == "sqlite" and self.sqlite_available

    def get(self, name: str):
        if name == "sqlite":
            return self.sqlite_driver
        return None

    def statuses(self) -> dict:
        return {}


class _Delegate:
    def __init__(self, *, result=None, failure=None) -> None:
        self.result = result
        self.failure = failure
        self.calls = 0

    async def promote(self, **kwargs):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.result


def _engine(sqlite_driver=None):
    engine = StorageEngine(SimpleNamespace())
    drivers = _FakeDrivers(sqlite_driver)
    engine.drivers = drivers
    engine._initialize_drivers = lambda: None
    engine._initialize_repositories = lambda: None
    return engine, drivers


def _assert_public_sqlite_session_source(service, sqlite_driver) -> None:
    delegate = service._delegate
    orchestration = delegate._orchestration
    handoff = orchestration._reservation_handoff

    recovery = handoff._reservation_recovery
    assert isinstance(recovery, DurablePromotionReservationRecovery)

    session_factories = (
        orchestration._source_authority._session_factory,
        handoff._reservation_issuer._session_factory,
        recovery._session_factory,
        delegate._admission._session_factory,
    )
    assert all(factory.__self__ is sqlite_driver for factory in session_factories)
    assert all(
        factory.__func__ is _FakeSQLiteDriver.get_session
        for factory in session_factories
    )


@pytest.mark.asyncio
async def test_b6_activation_requires_available_configured_sqlite() -> None:
    engine, _drivers = _engine()

    await engine.connect()
    try:
        assert SERVICE_KEY not in engine.services
    finally:
        await engine.disconnect()


@pytest.mark.asyncio
async def test_b6_activation_publishes_one_guarded_canonical_service() -> None:
    sqlite_driver = _FakeSQLiteDriver()
    engine, _drivers = _engine(sqlite_driver)

    await engine.connect()
    try:
        assert list(engine.services) == [SERVICE_KEY]
        service = engine.services[SERVICE_KEY]
        _assert_public_sqlite_session_source(service, sqlite_driver)
        assert sqlite_driver.session_entries == 0
    finally:
        await engine.disconnect()


@pytest.mark.asyncio
async def test_b6_generation_guard_rejects_retained_service_before_delegate_or_sql() -> None:
    sqlite_driver = _FakeSQLiteDriver()
    engine, _drivers = _engine(sqlite_driver)

    await engine.connect()
    first_service = engine.services[SERVICE_KEY]
    first_generation = first_service._generation

    active_delegate = _Delegate(result="active")
    first_service._delegate = active_delegate
    assert (
        await first_service.promote(
            source_ref=object(),
            owner_user_id="owner-b6",
        )
        == "active"
    )
    assert active_delegate.calls == 1
    assert sqlite_driver.session_entries == 0

    await engine.disconnect()

    with pytest.raises(
        StorageServiceGenerationRevokedError,
        match="generation is no longer active",
    ):
        await first_service.promote(
            source_ref=object(),
            owner_user_id="owner-b6",
        )

    assert active_delegate.calls == 1
    assert sqlite_driver.session_entries == 0

    await engine.connect()
    try:
        second_service = engine.services[SERVICE_KEY]
        assert second_service is not first_service
        assert second_service._generation > first_generation

        fresh_delegate = _Delegate(result="fresh")
        second_service._delegate = fresh_delegate
        assert (
            await second_service.promote(
                source_ref=object(),
                owner_user_id="owner-b6",
            )
            == "fresh"
        )
        assert fresh_delegate.calls == 1

        with pytest.raises(StorageServiceGenerationRevokedError):
            await first_service.promote(
                source_ref=object(),
                owner_user_id="owner-b6",
            )
        assert active_delegate.calls == 1
        assert sqlite_driver.session_entries == 0
    finally:
        await engine.disconnect()


@pytest.mark.asyncio
async def test_b6_startup_failure_revokes_published_generation_before_teardown() -> None:
    sqlite_driver = _FakeSQLiteDriver()
    engine, drivers = _engine(sqlite_driver)
    retained = {}
    active_generation_seen_at_disconnect = []

    drivers.on_disconnect = lambda: active_generation_seen_at_disconnect.append(
        engine._active_service_generation
    )

    def fail_after_service_publication() -> None:
        retained["service"] = engine.services[SERVICE_KEY]
        raise RuntimeError("repository initialization failed")

    engine._initialize_repositories = fail_after_service_publication

    with pytest.raises(RuntimeError, match="repository initialization failed"):
        await engine.connect()

    service = retained["service"]
    delegate = _Delegate(result="must-not-run")
    service._delegate = delegate

    assert active_generation_seen_at_disconnect == [None]
    assert engine.services == {}

    with pytest.raises(StorageServiceGenerationRevokedError):
        await service.promote(
            source_ref=object(),
            owner_user_id="owner-b6",
        )
    assert delegate.calls == 0
    assert sqlite_driver.session_entries == 0


@pytest.mark.asyncio
async def test_b6_guard_preserves_delegate_error_and_cancellation_semantics() -> None:
    sqlite_driver = _FakeSQLiteDriver()
    engine, _drivers = _engine(sqlite_driver)

    await engine.connect()
    try:
        service = engine.services[SERVICE_KEY]

        runtime_failure = RuntimeError("lower-layer failure")
        failing_delegate = _Delegate(failure=runtime_failure)
        service._delegate = failing_delegate
        with pytest.raises(RuntimeError, match="lower-layer failure") as error:
            await service.promote(
                source_ref=object(),
                owner_user_id="owner-b6",
            )
        assert error.value is runtime_failure
        assert failing_delegate.calls == 1

        cancellation = asyncio.CancelledError()
        cancelling_delegate = _Delegate(failure=cancellation)
        service._delegate = cancelling_delegate
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await service.promote(
                source_ref=object(),
                owner_user_id="owner-b6",
            )
        assert cancelled.value is cancellation
        assert cancelling_delegate.calls == 1
    finally:
        await engine.disconnect()
