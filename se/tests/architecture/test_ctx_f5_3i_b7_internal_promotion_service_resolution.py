import ast
from pathlib import Path


MANAGER = Path("se/src/infrastructure/storage/core/manager.py")
CONTAINER = Path("se/src/application/container.py")
MAIN = Path("se/src/main.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method_source(source: str, class_name: str, method_name: str) -> str:
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    segment = ast.get_source_segment(source, method)
    assert segment is not None
    return segment


def test_b7_resolver_is_bounded_to_existing_storage_service_registry() -> None:
    manager = _read(MANAGER)
    method = _method_source(
        manager,
        "StorageEngine",
        "get_tool_response_payload_memory_promotion",
    )

    assert "_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION" in method
    assert "_GenerationBoundToolResponsePayloadMemoryPromotion" in method
    assert "_active_service_generation" in method
    assert "_is_service_generation_active" in method
    assert "self.services.get(" in method

    for forbidden in (
        ".promote(",
        "get_session",
        "async_sessionmaker",
        "create_async_engine",
        "ApplicationContainer",
        "FastAPI",
        "capability_id",
        "invocation_id",
        "ContextBuilder",
    ):
        assert forbidden not in method


def test_b7_does_not_add_application_or_runtime_promotion_wiring() -> None:
    manager = _read(MANAGER)
    container = _read(CONTAINER)
    main = _read(MAIN)

    assert "DurableToolResponsePayloadMemoryPromotion" in manager
    assert "get_tool_response_payload_memory_promotion" in manager

    for source in (container, main):
        assert "get_tool_response_payload_memory_promotion" not in source
        assert "DurableToolResponsePayloadMemoryPromotion" not in source


def test_b7_preserves_no_automatic_promotion_boundary() -> None:
    method = _method_source(
        _read(MANAGER),
        "StorageEngine",
        "get_tool_response_payload_memory_promotion",
    )

    assert "return service" in method
    assert "await " not in method
    assert ".promote(" not in method
