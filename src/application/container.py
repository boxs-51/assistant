from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class ApplicationContainer:
    """Application-wide dependency graph owned by the bootstrap process."""

    config: Any
    storage: Any
    uow_factory: Callable[[], Any]
    http_client: Any
    eventing_manager: Any
    runtime_kernel: Optional[Any] = None
    provider_runtime: Optional[Any] = None
    session_runtime: Optional[Any] = None
    context_runtime: Optional[Any] = None
    capability_runtime: Optional[Any] = None
    workflow_runtime: Optional[Any] = None
    connection_runtime: Optional[Any] = None
    event_runtime: Optional[Any] = None
    auth_manager: Optional[Any] = None
    oauth: Optional[Any] = None
    limiter: Optional[Any] = None
    agent_registry: Optional[Any] = None
    tool_registry: Optional[Any] = None
    multi_agent_coordinator: Optional[Any] = None

    @property
    def event_bus(self):
        return self.eventing_manager.bus

    def get(self, name: str, default: Any = None) -> Any:
        return getattr(self, name, default)

    def resolve(self, dependency_type: type) -> Any:
        for value in self.__dict__.values():
            if isinstance(value, dependency_type):
                return value
        context_runtime = self.context_runtime
        context_engine = getattr(context_runtime, "context_engine", None)
        if isinstance(context_engine, dependency_type):
            return context_engine
        return None

    def require(self, name: str) -> Any:
        value = self.get(name)
        if value is None:
            raise RuntimeError(f"Application dependency '{name}' is not initialized.")
        return value
