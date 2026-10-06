from __future__ import annotations

import ast
from pathlib import Path


ROUTER = Path("se/src/transport/gateway/api/v1/session_router.py")
SOURCE_ROOT = Path("se/src")
CALLER = "promote_tool_response_payload_memory_for_session"
CALL_TOKEN = ".promote_tool_response_payload_memory("


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str) -> ast.AsyncFunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function {name} not found")


def _normalized_function(source: str, name: str) -> str:
    node = _function(source, name)
    segment = ast.get_source_segment(source, node)
    assert segment is not None
    return " ".join(segment.split())


def test_p3_freezes_exact_route_and_function_signature() -> None:
    source = _read(ROUTER)
    function = _function(source, CALLER)

    assert [arg.arg for arg in function.args.args] == [
        "session_id",
        "source_ref",
        "identity",
        "container",
    ]
    assert function.args.vararg is None
    assert function.args.kwarg is None
    assert [ast.unparse(arg.annotation) for arg in function.args.args] == [
        "str",
        "ContextSourceRef",
        "Identity",
        "ApplicationContainer",
    ]
    assert ast.unparse(function.returns) == "MemoryRecord"

    decorators = [
        item
        for item in function.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "router"
        and item.func.attr == "post"
    ]
    assert len(decorators) == 1
    route = decorators[0]
    assert len(route.args) == 1
    assert isinstance(route.args[0], ast.Constant)
    assert (
        route.args[0].value
        == "/{session_id}/memory/promotions/tool-response"
    )
    response_model = {
        keyword.arg: keyword.value for keyword in route.keywords
    }["response_model"]
    assert isinstance(response_model, ast.Name)
    assert response_model.id == "MemoryRecord"


def test_p3_entry_authority_is_fail_closed_and_ordered() -> None:
    source = _read(ROUTER)
    normalized = _normalized_function(source, CALLER)

    required = (
        'principal = str(identity.user_id or "").strip()',
        "if not principal:",
        "status_code=status.HTTP_403_FORBIDDEN",
        "await _owned_session(container, session_id, identity)",
        "source_ref.source_kind is not ContextSourceKind.TOOL_RESPONSE_PAYLOAD",
        "status_code=status.HTTP_422_UNPROCESSABLE_ENTITY",
        "source_ref.session_id != session_id",
        "status_code=status.HTTP_409_CONFLICT",
        "await container.storage.promote_tool_response_payload_memory(",
        "source_ref=source_ref",
        "owner_user_id=principal",
    )
    for phrase in required:
        assert phrase in normalized

    assert normalized.index("if not principal:") < normalized.index(
        "await _owned_session(container, session_id, identity)"
    )
    assert normalized.index(
        "await _owned_session(container, session_id, identity)"
    ) < normalized.index(
        "source_ref.source_kind is not ContextSourceKind.TOOL_RESPONSE_PAYLOAD"
    )
    assert normalized.index(
        "source_ref.session_id != session_id"
    ) < normalized.index(
        "await container.storage.promote_tool_response_payload_memory("
    )


def test_p3_has_exactly_one_production_caller_and_one_await() -> None:
    matches = [
        path.as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if CALL_TOKEN in _read(path)
    ]
    assert matches == [ROUTER.as_posix()]

    source = _read(ROUTER)
    function = _function(source, CALLER)
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "promote_tool_response_payload_memory"
    ]
    assert len(calls) == 1

    awaited_calls = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "promote_tool_response_payload_memory"
    ]
    assert awaited_calls == calls


def test_p3_adds_no_retry_background_event_or_routing_authority() -> None:
    source = _read(ROUTER)
    function = _function(source, CALLER)
    segment = ast.get_source_segment(source, function)
    assert segment is not None

    assert not any(isinstance(node, ast.Try) for node in ast.walk(function))
    assert not any(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) for node in ast.walk(function))

    for forbidden in (
        "create_task(",
        "ensure_future(",
        "event_bus",
        ".publish(",
        ".subscribe(",
        "resolve_budget_owner(",
        "CapabilityRoutingPolicy",
        "AgentRuntime",
        "DirectChatRuntime",
        "TaskBudget",
    ):
        assert forbidden not in segment
