import ast
from pathlib import Path


MANAGER = Path("se/src/infrastructure/storage/core/manager.py")
SOURCE_ROOT = Path("se/src")
P3_CALLER = Path("se/src/transport/gateway/api/v1/session_router.py")
P3_FUNCTION = "promote_tool_response_payload_memory_for_session"
METHOD = "promote_tool_response_payload_memory"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method_node(source: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(source)
    storage = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StorageEngine"
    )
    method = next(
        node
        for node in storage.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == METHOD
    )
    return method


def _method_source(source: str) -> str:
    method = _method_node(source)
    segment = ast.get_source_segment(source, method)
    assert segment is not None
    return segment


def test_b8_p1_handoff_has_exact_awaited_resolve_then_promote_shape() -> None:
    source = _read(MANAGER)
    method = _method_node(source)
    segment = _method_source(source)

    assert [arg.arg for arg in method.args.kwonlyargs] == [
        "source_ref",
        "owner_user_id",
    ]
    assert method.args.vararg is None
    assert method.args.kwarg is None

    resolver_calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_tool_response_payload_memory_promotion"
    ]
    assert len(resolver_calls) == 1

    promote_awaits = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "promote"
    ]
    assert len(promote_awaits) == 1

    call = promote_awaits[0].value
    assert [keyword.arg for keyword in call.keywords] == [
        "source_ref",
        "owner_user_id",
    ]
    assert isinstance(call.keywords[0].value, ast.Name)
    assert call.keywords[0].value.id == "source_ref"
    assert isinstance(call.keywords[1].value, ast.Name)
    assert call.keywords[1].value.id == "owner_user_id"

    assert "return await service.promote(" in segment
    assert segment.index(
        "self.get_tool_response_payload_memory_promotion()"
    ) < segment.index("await service.promote(")


def test_b8_p1_handoff_contains_no_parallel_authority_or_scheduling() -> None:
    segment = _method_source(_read(MANAGER))

    for forbidden in (
        "self.services",
        "self.drivers",
        "get_session",
        "sessionmaker",
        "repository",
        "UnitOfWork",
        "create_task",
        "TaskGroup",
        "retry",
        "timeout",
        "capability_id",
        "invocation_id",
        "Identity",
        "ContextBuilder",
    ):
        assert forbidden not in segment


def test_b8_p1_historical_zero_caller_is_superseded_only_by_released_p3() -> None:
    callers: list[tuple[str, str]] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == METHOD
                for child in ast.walk(node)
            ):
                callers.append((path.as_posix(), node.name))

    assert callers == [(P3_CALLER.as_posix(), P3_FUNCTION)]

    source = _read(P3_CALLER)
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == P3_FUNCTION
    )
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == METHOD
    ]
    assert len(calls) == 1
    assert ast.unparse(calls[0].func) == (
        "container.storage.promote_tool_response_payload_memory"
    )
