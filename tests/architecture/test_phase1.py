import ast
import asyncio
from pathlib import Path

import pytest
from src.application.container import ApplicationContainer
from src.domain.schemas.event import BaseEvent
from src.infrastructure.event_bus import subscribers
from src.infrastructure.event_bus.bus import EventBus, EventPriority
from src.infrastructure.event_bus.registry import EventRegistry
from src.kernel.base import RuntimeContext


class EventingStub:
    def __init__(self):
        self.bus = object()


def test_application_container_resolves_registered_dependencies():
    provider_runtime = object()
    container = ApplicationContainer(
        config={},
        storage=object(),
        uow_factory=lambda: None,
        http_client=object(),
        eventing_manager=EventingStub(),
        provider_runtime=provider_runtime,
        event_bus=EventingStub().bus,
    )

    assert container.require("provider_runtime") is provider_runtime
    assert container.get("missing", "fallback") == "fallback"


def test_builtin_subscribers_use_the_shared_registry():
    registry = EventRegistry()
    subscribers.register_subscribers(registry)

    assert len(registry.get_handlers("user.created")) == 2
    assert len(registry.get_handlers("system.event.failed")) == 2
    assert len(registry.get_handlers("unknown.event")) == 1


@pytest.mark.asyncio
async def test_equal_priority_events_are_orderable_without_comparing_events():
    registry = EventRegistry()
    bus = EventBus(registry, {"test.event": EventPriority.NORMAL})

    first = BaseEvent(event_name="test.event", session_id=None)
    second = BaseEvent(event_name="test.event", session_id=None)
    bus.publish(first)
    bus.publish(second)

    first_item = bus.queue.get_nowait()
    second_item = bus.queue.get_nowait()
    assert first_item[0] == second_item[0] == EventPriority.NORMAL
    assert first_item[1] < second_item[1]
    assert first_item[2] is first
    assert second_item[2] is second


def test_container_bind_runtime_identity():
    container = ApplicationContainer(
        config={},
        storage=object(),
        uow_factory=lambda: None,
        http_client=object(),
        eventing_manager=EventingStub(),
        event_bus=EventingStub().bus,
    )
    runtime_obj = object()
    container.bind_runtime("custom_runtime", runtime_obj)

    assert container.get("custom_runtime") is runtime_obj
    assert container.require("custom_runtime") is runtime_obj


def test_runtime_context_first_class_dependency():
    container = ApplicationContainer(
        config={},
        storage=object(),
        uow_factory=lambda: None,
        http_client=object(),
        eventing_manager=EventingStub(),
        event_bus=EventingStub().bus,
    )
    runtime_context = RuntimeContext(
        kernel=object(),
        container=container,
        config={},
        logger=object(),
        event_bus=object(),
        storage=container.storage,
        uow_factory=container.uow_factory,
        http_client=container.http_client,
    )

    assert runtime_context.container is container


def test_ast_guard_forbids_direct_app_state_access():
    project_root = Path(__file__).resolve().parents[2]
    src_dir = project_root / "src"

    # Entrypoints, middleware, and pending legacy modules excluded from app.state linting
    excluded_files = {
        "main.py",
        "dependencies.py",
        "middleware.py",
        "admin_service.py",
        "chat_router.py",
        "embeddings_router.py",
        "files_router.py",
        "health_router.py",
        "models_router.py",
    }

    violations = []

    for path in src_dir.rglob("*.py"):
        if path.name in excluded_files:
            continue

        file_contents = path.read_text(encoding="utf-8")
        parsed_ast = ast.parse(file_contents, filename=str(path))

        class AppStateVisitor(ast.NodeVisitor):
            def visit_Attribute(self, node: ast.Attribute):
                # Detect `request.app.state` or `app.state`
                if node.attr == "state":
                    if isinstance(node.value, ast.Attribute) and node.value.attr == "app":
                        violations.append(f"{path}:{node.lineno}: direct access to request.app.state")
                    elif isinstance(node.value, ast.Name) and node.value.id == "app":
                        violations.append(f"{path}:{node.lineno}: direct access to app.state")
                self.generic_visit(node)

        AppStateVisitor().visit(parsed_ast)

    assert not violations, "Forbidden direct app.state access detected:\n" + "\n".join(violations)


def test_kernel_container_shared_identity():
    container = ApplicationContainer(
        config={},
        storage=object(),
        uow_factory=lambda: None,
        http_client=object(),
        eventing_manager=EventingStub(),
        event_bus=EventingStub().bus,
    )
    kernel_stub = object()
    container.bind_runtime("kernel", kernel_stub)

    assert container.get("kernel") is kernel_stub