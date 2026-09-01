from contextlib import asynccontextmanager
from typing import Dict, Any, Tuple
from fastapi import FastAPI
import httpx
import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .infrastructure.event_bus.manager import EventingManager

from .kernel.kernel import RuntimeKernel

# Config & Observability
from .infrastructure.config import ConfigurationRegistry, ConfigManager, ConfigSchema
from .infrastructure.observability import ObservabilityConfig, LoggingConfig, TracingConfig

from .transport.gateway.middleware.metris import setup_gateway_observability
from .transport.gateway.middleware.factory import create_middleware_stack

# Storage & UoW
from .infrastructure.storage.core.manager import StorageEngine
from .infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .infrastructure.mcp.mcp_manager import GatewayMcpManager

# Security & Gateway Infrastructure
from .transport.gateway.limiter import RateLimiterManager
from .circuit_breaker import CircuitBreakerManager

from .transport.gateway.authentication.oauth import create_oauth_client
from .transport.gateway.authentication.manager import AuthenticationManager
from .transport.gateway.authentication.authentication import Authentication

from .transport.gateway.authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .transport.gateway.authentication.authenticators.jwt_authenticator import JWTAuthenticator

from .transport.gateway.authentication.services import (APIKeyService, LoginService, OAuthService,
OTPStorageService, RegistrationService, TokenService, UserService)

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
from .application.policy.authorization import AuthorizationService
from .agent.registry import AgentRegistry
from .tool.registry import ToolRegistry
from .runtimes.capability.registry import CapabilityRegistry
from .runtimes.agent.coordinator import MultiAgentCoordinator
from .runtimes.agent.persistence import DurableAgentStore
from .runtimes.agent.adapters import (
    ContextBuilderAdapter,
    ProviderInferenceAdapter,
    CapabilityToolExecutionAdapter,
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from .runtimes.agent.tool_execution import AgentToolExecutionCoordinator
logger = structlog.get_logger(__name__)


# ==============================================================================
# BOOTSTRAP FACTORIES
# ==============================================================================

def bootstrap_observability() -> ConfigSchema:
    """Tải cấu hình gateway và kích hoạt hệ thống Observability (Metrics & Tracing)."""
    _config_manager = ConfigManager().get_instance("config/gateway/default.yaml")
    _config = _config_manager.initialize()
    ConfigurationRegistry.set_config(_config)

    obs_config = ObservabilityConfig(
        service_name=_config.gateway.name,
        service_version=_config.gateway.version,
        logging=LoggingConfig(level=_config.logging.level),
        tracing=TracingConfig(
            enable=_config.tracing.enable,
            otlp_endpoint=_config.tracing.otlp_endpoint,
        ),
    )
    setup_gateway_observability(obs_config)
    return _config


async def bootstrap_storage(config: ConfigSchema) -> Tuple[StorageEngine, Any]:
    """Kết nối cơ sở dữ liệu và tạo Unit of Work Factory."""
    storage_engine = StorageEngine(config)
    await storage_engine.connect()
    
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
    
    logger.info("Storage Engine connected successfully.")
    return storage_engine, uow_factory


def bootstrap_security(
    config: ConfigSchema,
    storage_engine: StorageEngine, 
    uow_factory: Any, 
    cb_manager: CircuitBreakerManager,
    eventing_manager: EventingManager,
) -> Dict[str, Any]:
    """Khởi tạo các dịch vụ Xác thực, OAuth và Rate Limiting."""
    cache_driver = storage_engine.get_cache_driver()
    
    limiter = RateLimiterManager(
        cache_driver=cache_driver,
        circuit_breaker_manager=cb_manager,
        config=config.rate_limit
    )

    session_repo = storage_engine.repositories.get("sessions")
    token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo,config=config.auth)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    auth_manager = AuthenticationManager(
        authenticators=[
            APIKeyAuthenticator(api_key_service),
            JWTAuthenticator(token_service, uow_factory),
        ]
    )
    redis_driver = storage_engine.get_cache_driver()
    otp_service = OTPStorageService(redis_driver if redis_driver else None, uow_factory)
    registration_service = RegistrationService(uow_factory, otp_service, token_service, eventing_manager.bus)
    login_service = LoginService(uow_factory, token_service)
    oauth_service = OAuthService(uow_factory, token_service, eventing_manager.bus)
    user_service = UserService(uow_factory)

    auth = Authentication(
        registration_service=registration_service,
        login_service=login_service,
        oauth_service=oauth_service,
        token_service=token_service,
        user_service=user_service,
    )

    oauth = create_oauth_client(config.oauth)
    logger.info("Authentication & Security Managers initialized.")
    return {
        "auth_manager": auth_manager,
        "oauth": oauth,
        "limiter": limiter,
        "auth": auth,
        "api_key_service": api_key_service,
    }


async def bootstrap_runtime_kernel(
    config: Any,
    storage_engine: StorageEngine,
    uow_factory: Any,
    http_client: httpx.AsyncClient,
    cb_manager: CircuitBreakerManager,
    eventing_manager: EventingManager,

    security_services: Dict[str, Any] = None,
) -> ApplicationContainer:
    """Khởi tạo EventBus, Container, các Runtimes và kích hoạt Boot Sequence cho Kernel."""
    eventing_manager.register_subscribers()
    agent_registry = AgentRegistry()
    capability_registry = CapabilityRegistry()
    authorization_service = AuthorizationService()

    # 1. Tạo ApplicationContainer trước
    container = ApplicationContainer(
        config=config,
        storage=storage_engine,
        uow_factory=uow_factory,
        http_client=http_client,
        eventing_manager=eventing_manager,
        mcp_manager=GatewayMcpManager(),
        event_bus=eventing_manager.bus,
        circuit_breaker_manager=cb_manager,
        agent_registry=agent_registry,
        tool_registry=ToolRegistry(),
        capability_registry=capability_registry,
        authorization_service=authorization_service,
        multi_agent_coordinator=MultiAgentCoordinator(
            agent_registry,
            durable_store=DurableAgentStore(eventing_manager.uow_factory),
        ),
        **(security_services or {}),
    )
    eventing_manager.set_dependency_container(container)
    await container.mcp_manager.start_health_checker()

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
        (
            "capability_runtime",
            CapabilityRuntime(
                registry=capability_registry,
                authorization=authorization_service,
            ),
        ),
        ("provider_runtime", ProviderRuntime(cb_manager)),
    ]

    for runtime_id, runtime_instance in runtimes:
        container.bind_runtime(runtime_id, runtime_instance)
        kernel.register_runtime(runtime_instance)

    if container.capability_runtime and hasattr(container.capability_runtime, "registry"):
        container.tool_registry = ToolRegistry(container.capability_runtime.registry)

    # 4. Bootstrap Kernel (RuntimeContext tự động được khởi tạo bên trong)
    await kernel.bootstrap()

    # Phase 5.1-5.4: establish canonical ports without changing the
    # existing Phase 4 coordinator execution path. AgentRuntime is not
    # implemented yet and remains the future consumer of these ports.
    agent_tool_policy = RegistryAgentToolPolicy(
        agent_registry=container.agent_registry,
        capability_registry=container.capability_registry,
        authorization=container.authorization_service,
    )
    agent_execution_policy = DefaultAgentExecutionPolicy()
    container.agent_tool_policy = agent_tool_policy
    container.agent_execution_policy = agent_execution_policy
    container.context_builder_port = ContextBuilderAdapter(
        container.context_runtime,
        container.capability_runtime,
        agent_tool_policy,
    )
    container.inference_port = ProviderInferenceAdapter(
        container.provider_runtime,
        container.http_client,
    )
    container.tool_execution_port = CapabilityToolExecutionAdapter(
        container.capability_runtime,
        agent_tool_policy,
        agent_execution_policy,
    )
    container.tool_execution_port = AgentToolExecutionCoordinator(
        container.tool_execution_port,
    )

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

    # Compatibility execution path remains intact until Phase 5.5.
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
    config = bootstrap_observability()
    FastAPIInstrumentor.instrument_app(app)

    # Local resources
    cb_manager = CircuitBreakerManager(config=config.circuit_breaker)
    http_client = httpx.AsyncClient(timeout=config.provider.timeout)
    storage_engine, uow_factory = await bootstrap_storage(config)
    eventing_manager = EventingManager(storage_engine=storage_engine)
    security_services = bootstrap_security(config=config, storage_engine=storage_engine, 
                                           uow_factory=uow_factory, cb_manager=cb_manager,
                                           eventing_manager=eventing_manager)
    
    container = await bootstrap_runtime_kernel(
        config=config, storage_engine=storage_engine, uow_factory=uow_factory, 
        http_client=http_client, cb_manager=cb_manager, security_services=security_services, 
        eventing_manager=eventing_manager
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

        if container.mcp_manager:
            await container.mcp_manager.stop()

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