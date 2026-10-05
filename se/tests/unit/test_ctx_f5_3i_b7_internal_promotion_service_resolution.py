import pytest

from se.src.infrastructure.storage.core.manager import (
    StorageEngine,
    StorageServiceGenerationRevokedError,
    _GenerationBoundToolResponsePayloadMemoryPromotion,
    _TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION,
)


class _Drivers:
    def __init__(self, *, sqlite_available: bool) -> None:
        self._sqlite_available = sqlite_available

    def is_available(self, name: str) -> bool:
        return name == "sqlite" and self._sqlite_available


def _engine(
    *,
    started: bool,
    active_generation: int | None,
    sqlite_available: bool = True,
) -> StorageEngine:
    engine = StorageEngine.__new__(StorageEngine)
    engine.services = {}
    engine.drivers = _Drivers(  # type: ignore[assignment]
        sqlite_available=sqlite_available
    )
    engine._started = started
    engine._service_generation = active_generation or 0
    engine._active_service_generation = active_generation
    return engine


def _service(
    *,
    generation: int,
) -> _GenerationBoundToolResponsePayloadMemoryPromotion:
    return _GenerationBoundToolResponsePayloadMemoryPromotion(
        object(),  # type: ignore[arg-type]
        generation=generation,
        is_generation_active=lambda candidate: candidate == generation,
    )


def test_b7_resolver_returns_only_current_generation_bound_service() -> None:
    engine = _engine(started=True, active_generation=7)
    service = _service(generation=7)
    engine.services[_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION] = service

    assert engine.get_tool_response_payload_memory_promotion() is service


@pytest.mark.parametrize(
    ("started", "active_generation"),
    [
        (False, None),
        (False, 7),
        (True, None),
    ],
)
def test_b7_resolver_rejects_inactive_storage_generation(
    started: bool,
    active_generation: int | None,
) -> None:
    engine = _engine(
        started=started,
        active_generation=active_generation,
    )

    with pytest.raises(StorageServiceGenerationRevokedError):
        engine.get_tool_response_payload_memory_promotion()


def test_b7_resolver_rejects_missing_or_noncanonical_registry_entry() -> None:
    engine = _engine(started=True, active_generation=7)

    with pytest.raises(RuntimeError, match="unavailable"):
        engine.get_tool_response_payload_memory_promotion()

    engine.services[_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION] = object()

    with pytest.raises(RuntimeError, match="unavailable"):
        engine.get_tool_response_payload_memory_promotion()


def test_b7_resolver_rejects_stale_generation_service() -> None:
    engine = _engine(started=True, active_generation=8)
    engine.services[_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION] = _service(
        generation=7
    )

    with pytest.raises(StorageServiceGenerationRevokedError):
        engine.get_tool_response_payload_memory_promotion()


def test_b7_resolver_rejects_unavailable_sqlite_backend() -> None:
    engine = _engine(
        started=True,
        active_generation=7,
        sqlite_available=False,
    )
    engine.services[_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION] = _service(
        generation=7
    )

    with pytest.raises(RuntimeError, match="SQLite driver is unavailable"):
        engine.get_tool_response_payload_memory_promotion()
