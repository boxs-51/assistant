from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class ApplicationContainer:
    """Application-wide dependency graph owned by the bootstrap process.

    ``app.state.container`` is the only FastAPI application-state attachment.
    Application/runtime code must resolve dependencies from this object rather
    than from individual ``app.state`` fields.
    """

    config: Any
    storage: Any
    uow_factory: Callable[[], Any]
    http_client: Any
    eventing_manager: Any
    event_bus: Any
    
    mcp_manager: Optional[Any] = None
    runtime_kernel: Optional[Any] = None
    event_runtime: Optional[Any] = None
    context_runtime: Optional[Any] = None
    connection_runtime: Optional[Any] = None
    session_runtime: Optional[Any] = None
    workflow_runtime: Optional[Any] = None
    capability_runtime: Optional[Any] = None
    capability_registry: Optional[Any] = None
    authorization_service: Optional[Any] = None
    provider_runtime: Optional[Any] = None

    api_key_service: Optional[Any] = None
    auth_manager: Optional[Any] = None
    auth: Optional[Any] = None
    oauth: Optional[Any] = None
    limiter: Optional[Any] = None
    circuit_breaker_manager: Optional[Any] = None

    agent_registry: Optional[Any] = None
    tool_registry: Optional[Any] = None
    multi_agent_coordinator: Optional[Any] = None
    agent_tool_policy: Optional[Any] = None
    agent_execution_policy: Optional[Any] = None
    inference_port: Optional[Any] = None
    tool_execution_port: Optional[Any] = None
    context_builder_port: Optional[Any] = None

    metrics: Optional[Any] = None
    tracer: Optional[Any] = None
    clock: Optional[Any] = None


    def bind_runtime(self, runtime_id: str, runtime: Any) -> None:
        if not runtime_id:
            raise ValueError("runtime_id must not be empty")
        if runtime is None:
            raise ValueError(f"runtime '{runtime_id}' must not be None")
        setattr(self, runtime_id, runtime)

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

    def get_dependency(self, dependency_type: type) -> Any:
        """Giải phóng dependency instance từ container hoặc trả về Class Type 
        để EventDispatcher tự bind thông qua UnitOfWork.
        """
        # 1. Tìm instance đã khởi tạo trong container (VD: Config, Cache, Runtimes)
        instance = self.resolve(dependency_type)
        if instance is not None:
            return instance

        # 2. Nếu là Repository Type (chưa có instance trong Container vì nằm trong UoW),
        # trả về type class để EventDispatcher đẩy vào repo_types_needed
        if isinstance(dependency_type, type):
            return dependency_type

        return None

    def require(self, name: str) -> Any:
        value = self.get(name)
        if value is None:
            raise RuntimeError(f"Application dependency '{name}' is not initialized.")
        return value
