from __future__ import annotations

import ast
from pathlib import Path

SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_memory_promotion.py"
)


def _read() -> str:
    return SERVICE.read_text(encoding="utf-8")


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function(cls: ast.ClassDef, name: str):
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"function {name} not found")


def test_b5_surface_is_one_thin_composer_only() -> None:
    source = _read()
    tree = ast.parse(source)
    cls = _class(source, "DurableToolResponsePayloadMemoryPromotion")
    methods = {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert methods == {"__init__", "promote"}

    imports = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)

    assert imports == {
        "__future__",
        "se.src.context.memory",
        "se.src.context.source_identity",
        "se.src.infrastructure.storage.services.memory_promotion_admission",
        (
            "se.src.infrastructure.storage.services."
            "tool_response_payload_promotion_orchestration"
        ),
    }


def test_b5_promote_has_exact_input_and_reserve_then_admit_order() -> None:
    source = _read()
    promote = _function(
        _class(source, "DurableToolResponsePayloadMemoryPromotion"),
        "promote",
    )

    assert isinstance(promote, ast.AsyncFunctionDef)
    assert [arg.arg for arg in promote.args.args] == ["self"]
    assert [arg.arg for arg in promote.args.kwonlyargs] == [
        "source_ref",
        "owner_user_id",
    ]
    assert promote.args.vararg is None
    assert promote.args.kwarg is None

    awaited = []
    for node in ast.walk(promote):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            function = node.value.func
            if isinstance(function, ast.Attribute):
                awaited.append(function.attr)

    assert awaited == ["reserve", "admit"]

    snapshots = [
        node
        for node in ast.walk(promote)
        if isinstance(node, ast.Attribute) and node.attr == "content_snapshot"
    ]
    assert len(snapshots) == 1

    reserve_calls = [
        node
        for node in ast.walk(promote)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reserve"
    ]
    admit_calls = [
        node
        for node in ast.walk(promote)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "admit"
    ]
    assert len(reserve_calls) == 1
    assert len(admit_calls) == 1
    assert [keyword.arg for keyword in reserve_calls[0].keywords] == [
        "source_ref",
        "owner_user_id",
    ]
    assert [keyword.arg for keyword in admit_calls[0].keywords] == [
        "reservation",
        "content",
    ]


def test_b5_does_not_acquire_closed_authority() -> None:
    source = _read()

    forbidden = (
        "DurableToolResponsePayloadSourceAuthority",
        "DurablePromotionReservationIssuer",
        "DurablePromotionReservationRecovery",
        "DurablePromotionReservationRepository",
        "DurableMemoryRecordRepository",
        "AsyncSession",
        "session_factory",
        "begin(",
        "commit(",
        "rollback(",
        "uuid",
        "create_memory_record",
        "MemoryRecord(",
        "ContextBuilder",
        "ApplicationContainer",
        "FastAPI",
        "TaskBudget",
        "FileAsset",
        "ObjectStorage",
    )
    for name in forbidden:
        assert name not in source

    tree = ast.parse(source)
    promote = _function(
        _class(source, "DurableToolResponsePayloadMemoryPromotion"),
        "promote",
    )
    assert not any(isinstance(node, ast.Try) for node in ast.walk(promote))
    assert not any(
        isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        for node in ast.walk(promote)
    )
