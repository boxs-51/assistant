from contextlib import asynccontextmanager
from typing import Dict, Any, Tuple
from fastapi import FastAPI
import httpx
import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .infrastructure.event_bus.manager import EventingManager

from .kernel.kernel import RuntimeKernel

# Config & Observability
from .infrastructure.config import settings, ConfigLoader, ConfigurationRegistry, ConfigManager
from .infrastructure.observability import ObservabilityConfig, LoggingConfig, TracingConfig

from .transport.gateway.middleware.metris import setup_gateway_observability
from .transport.gateway.middleware.factory import create_middleware_stack

# Storage & UoW
from .infrastructure.storage.core.manager import StorageEngine
from .infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork

# Security & Gateway Infrastructure
from .transport.gateway.limiter import RateLimiterManager
from .circuit_breaker import CircuitBreakerManager
from .transport.gateway.authentication.oauth import create_oauth_client
from .transport.gateway.authentication.manager import AuthenticationManager
from .transport.gateway.authentication.services.api_key_service import APIKeyService
from .transport.gateway.authentication.services.token_service import TokenService
from .transport.gateway.authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .transport.gateway.authentication.authenticators.jwt_authenticator import JWTAuthenticator

from .infrastructure.event_bus.bus import EventBus

from src.runtimes.connection.runtime import ConnectionRuntime
from src.runtimes.session.runtime import SessionRuntime
from src.runtimes.workflow.runtime import WorkflowRuntime
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.provider.runtime import ProviderRuntime
from src.runtimes.event.runtime import EventRuntime
from src.runtimes.context.runtime import ContextRuntime

# Canonical versioned HTTP transport routers
from .transport.gateway.api.v1 import (
    admin as admin_router,
    agent_router,
    auth_router,
    chat_router,
    embeddings_router,
    events_router,
    files_router,
    health_router,
    models_router,
    multi_agent_router,
    tool_router,
)
from .application.container import ApplicationContainer
from .agent.registry import AgentRegistry
from .tool.registry import ToolRegistry
from .runtimes.agent.coordinator import MultiAgentCoordinator
from .runtimes.agent.persistence import DurableAgentStore

logger = structlog.get_logger(__name__)


# ==============================================================================
# BOOTSTRAP FACTORIES
# ==============================================================================

def bootstrap_observability() -> Any:
    """Tải cấu hình gateway và kích hoạt hệ thống Observability (Metrics & Tracing)."""
    _config_manager = ConfigManager().get_instance()
    _config = _config_manager.initialize()
    ConfigurationRegistry.set_config(_config)

    obs_config = ObservabilityConfig(
        service_name=settings.gateway.name,
        service_version=settings.gateway.version,
        logging=LoggingConfig(level=settings.logging.level),
        tracing=TracingConfig(
            enable=settings.tracing.enable,
            otlp_endpoint=settings.tracing.otlp_endpoint,
        ),
    )
    setup_gateway_observability(obs_config)
    return _config, _config_manager


async def bootstrap_storage() -> Tuple[StorageEngine, Any]:
    """Kết nối cơ sở dữ liệu và tạo Unit of Work Factory."""
    storage_engine = StorageEngine()
    await storage_engine.connect()
    
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
    
    logger.info("Storage Engine connected successfully.")
    return storage_engine, uow_factory


def bootstrap_security(
    storage_engine: StorageEngine, 
    uow_factory: Any, 
    cb_manager: CircuitBreakerManager
) -> Dict[str, Any]:
    """Khởi tạo các dịch vụ Xác thực, OAuth và Rate Limiting."""
    cache_driver = storage_engine.get_cache_driver()
    
    limiter = RateLimiterManager(
        cache_driver=cache_driver,
        circuit_breaker_manager=cb_manager
    )

    session_repo = storage_engine.repositories.get("sessions")
    token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    auth_manager = AuthenticationManager(
        authenticators=[
            APIKeyAuthenticator(api_key_service),
            JWTAuthenticator(token_service, uow_factory),
        ]
    )
    oauth = create_oauth_client()
    logger.info("Authentication & Security Managers initialized.")
    return {
        "auth_manager": auth_manager,
        "oauth": oauth,
        "limiter": limiter,
    }


async def bootstrap_runtime_kernel(
    config: Any,
    storage_engine: StorageEngine,
    uow_factory: Any,
    http_client: httpx.AsyncClient,
    cb_manager: CircuitBreakerManager,
    security_services: Dict[str, Any] = None,
) -> ApplicationContainer:
    """Khởi tạo EventBus, Container, các Runtimes và kích hoạt Boot Sequence cho Kernel."""
    eventing_manager = EventingManager(storage_engine=storage_engine)
    eventing_manager.register_subscribers()
    agent_registry = AgentRegistry()
    
    # 1. Tạo ApplicationContainer trước
    container = ApplicationContainer(
        config=config,
        storage=storage_engine,
        uow_factory=eventing_manager.uow_factory,
        http_client=http_client,
        eventing_manager=eventing_manager,
        circuit_breaker_manager=cb_manager,
        agent_registry=agent_registry,
        tool_registry=ToolRegistry(),
        multi_agent_coordinator=MultiAgentCoordinator(
            agent_registry,
            durable_store=DurableAgentStore(eventing_manager.uow_factory),
        ),
        **(security_services or {}),
    )
    eventing_manager.set_dependency_container(container)

    # 2. Tạo RuntimeKernel nhận container
    kernel = RuntimeKernel(eventing_manager, container)
    container.runtime_kernel = kernel

    # 3. Tạo các instance Runtimes và Bind vào Container trước khi Kernel Bootstrap
    runtimes = [
        ("event_runtime", EventRuntime()),
        ("context_runtime", ContextRuntime()),
        ("connection_runtime", ConnectionRuntime()),
        ("session_runtime", SessionRuntime()),
        ("workflow_runtime", WorkflowRuntime()),
        ("capability_runtime", CapabilityRuntime()),
        ("provider_runtime", ProviderRuntime(cb_manager)),
    ]

    for runtime_id, runtime_instance in runtimes:
        container.bind_runtime(runtime_id, runtime_instance)
        kernel.register_runtime(runtime_instance)

    if container.capability_runtime and hasattr(container.capability_runtime, "registry"):
        container.tool_registry = ToolRegistry(container.capability_runtime.registry)

    # 4. Bootstrap Kernel (RuntimeContext tự động được khởi tạo bên trong)
    await kernel.bootstrap()

    # Cấu hình Multi-Agent Executor
    async def execute_registered_agent_task(task):
        agent = container.agent_registry.get(task.assigned_agent_id)
        if agent is None:
            raise LookupError(f"Agent '{task.assigned_agent_id}' is not registered.")
        request_body = dict(task.input)
        request_body.setdefault("model", request_body.get("model", ""))
        request_body["messages"] = [
            {"role": "system", "content": agent.instruction},
            *request_body.get("messages", [{"role": "user", "content": request_body.get("prompt", "")}]),
        ]
        response = await container.provider_runtime.chat_handler.execute_with_fallback(
            container.http_client, request_body
        )
        return response.model_dump()

    container.multi_agent_coordinator.executor = execute_registered_agent_task

    logger.info("AI Runtime Kernel & Runtimes booted successfully.")
    return container


# ==============================================================================
# APPLICATION LIFESPAN & CREATION
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý tập trung toàn bộ vòng đời ứng dụng (Startup & Graceful Shutdown)."""
    logger.info("Starting AI Gateway Application...")

    # 1. Startup Sequence
    config, config_manager = bootstrap_observability()
    FastAPIInstrumentor.instrument_app(app)

    # Local resources
    cb_manager = CircuitBreakerManager(config=config.circuit_breaker)
    http_client = httpx.AsyncClient(timeout=settings.provider.timeout)
    storage_engine, uow_factory = await bootstrap_storage()

    security_services = bootstrap_security(storage_engine, uow_factory, cb_manager)
    
    container = await bootstrap_runtime_kernel(
        config, storage_engine, uow_factory, http_client, cb_manager, security_services
    )

    # Chỉ gán duy nhất app.state.container
    app.state.container = container

    try:
        yield  # --- APPLICATION IS RUNNING AND SERVING TRAFFIC ---
    finally:
        # 2. Shutdown Sequence (Dọn dẹp trong try...finally)
        logger.info("Initiating Application Shutdown sequence...")

        if container.runtime_kernel:
            await container.runtime_kernel.shutdown()

        await http_client.aclose()
        await storage_engine.disconnect()

        logger.info("Shutdown sequence completed cleanly.")


def create_app() -> FastAPI:
    """Tạo instance FastAPI và đăng ký Middlewares, Routers."""
    app_instance = FastAPI(title="AI Gateway", lifespan=lifespan)

    # Middleware Stack
    create_middleware_stack(app_instance)

    # Route Registrations: api/v1 is the sole HTTP router surface.
    app_instance.include_router(auth_router.router)
    app_instance.include_router(files_router.router)
    app_instance.include_router(models_router.router)
    app_instance.include_router(chat_router.router)
    app_instance.include_router(embeddings_router.router)
    app_instance.include_router(admin_router.router)
    app_instance.include_router(agent_router.router)
    app_instance.include_router(tool_router.router)
    app_instance.include_router(events_router.router)
    app_instance.include_router(multi_agent_router.router)
    app_instance.include_router(health_router.router)

    return app_instance


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )