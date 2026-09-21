from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Tuple
import uuid
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
from .infrastructure.storage.repositories.capability_invocations import SqlCapabilityInvocationStore
from .infrastructure.mcp.mcp_manager import GatewayMcpManager

# Security & Gateway Infrastructure
from .transport.gateway.limiter import RateLimiterManager
from .circuit_breaker import CircuitBreakerManager

from .transport.gateway.authentication.oauth import create_oauth_client
from .transport.gateway.authentication.manager import AuthenticationManager
from .transport.gateway.authentication.authentication import Authentication

from .transport.gateway.authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .transport.gateway.authentication.authenticators.jwt_authenticator import JWTAuthenticator

from .transport.gateway.authentication.services import (APIKeyService, LoginService, OAuthService, PasswordResetService,
OTPStorageService, RegistrationService, TokenService, UserService, GuestSessionService)

from .runtimes.connection.runtime import ConnectionRuntime
from .runtimes.session.runtime import SessionRuntime
from .runtimes.workflow.runtime import WorkflowRuntime
from .runtimes.capability.runtime import CapabilityRuntime
from .runtimes.provider.runtime import ProviderRuntime
from .runtimes.event.runtime import EventRuntime
from .runtimes.context.runtime import ContextRuntime

# Canonical versioned HTTP transport routers
from .transport.gateway.api.v1 import (
    admin as admin_router,
    agent_router,
    auth_router,
    capability_router,
    chat_router,
    embeddings_router,
    events_router,
    files_router,
    health_router,
    models_router,
    multi_agent_router,
    session_router,
    tool_router,
)
from .application.container import ApplicationContainer
from .application.policy.authorization import AuthorizationService
from .agent.registry import AgentRegistry
from .tool.registry import ToolRegistry
from .runtimes.capability.registry import CapabilityRegistry
from .runtimes.capability.catalog import CapabilityCatalog
from .runtimes.capability.registration import ClientCapabilityRegistrationService
from .runtimes.capability.policy import CapabilityRoutingPolicy
from .runtimes.capability.local_tool_loader import register_local_tools
from .runtimes.capability.builtins import register_builtin_support
from .runtimes.capability.invocation import CapabilityInvocationLifecycle
from .runtimes.agent.coordinator import MultiAgentCoordinator
from .runtimes.agent.persistence import DurableAgentStore
from .runtimes.agent.runtime import AgentRuntime
from .runtimes.agent.supervisor import AgentExecutionSupervisor
from .runtimes.agent.task_budget import TaskBudgetService
from .runtimes.agent.continuation import AgentContinuationService
from .runtimes.agent.resume_planning import AgentResumePlanningService
from .runtimes.agent.ids import AgentExecutionIdFactory
from .runtimes.agent.assembly import DefaultAgentContextAssembler
from .runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from .runtimes.agent.capabilities import RegistryAgentCapabilityResolver, RegistryAgentSkillResolver
from .runtimes.agent.events import EventBusAgentEventPublisher
from .runtimes.agent.adapters import (
    ContextBuilderAdapter,
    ProviderInferenceAdapter,
    CapabilityToolExecutionAdapter,
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from .runtimes.agent.tool_execution import AgentToolExecutionCoordinator
from .runtimes.agent.contracts.context import AgentExecutionContext
from .runtimes.chat import DirectChatRuntime
from .domain.schemas.agent_execution import AgentExecutionLimits
from .domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from .domain.schemas.event import BaseEvent
from .version import __version__
logger = structlog.get_logger(__name__)


# ==============================================================================
# BOOTSTRAP FACTORIES
# ==============================================================================

def bootstrap_observability(config: ConfigSchema | None = None) -> ConfigSchema:
    """Tải cấu hình gateway và kích hoạt hệ thống Observability (Metrics & Tracing)."""
    _config = config
    if _config is None:
        _config_manager = ConfigManager().get_instance("se/config/default.yaml")
        _config = _config_manager.initialize()
    _config.auth.validate_runtime_secrets()
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
    guest_session_service = GuestSessionService(uow_factory, token_service)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    auth_manager = AuthenticationManager(
        authenticators=[
            APIKeyAuthenticator(api_key_service),
            JWTAuthenticator(token_service, uow_factory),
        ]
    )
    redis_driver = storage_engine.get_cache_driver()
    otp_service = OTPStorageService(redis_driver if redis_driver else None, uow_factory)
    registration_service = RegistrationService(
        uow_factory,
        otp_service,
        token_service,
        eventing_manager.bus,
        guest_session_service,
    )
    login_service = LoginService(uow_factory, token_service, guest_session_service)
    oauth_service = OAuthService(
        uow_factory,
        token_service,
        eventing_manager.bus,
        guest_session_service,
    )
    user_service = UserService(uow_factory)
    password_reset_service = PasswordResetService(uow_factory, otp_service)

    auth = Authentication(
        registration_service=registration_service,
        login_service=login_service,
        oauth_service=oauth_service,
        token_service=token_service,
        user_service=user_service,
        password_reset_service=password_reset_service,
    )

    oauth = create_oauth_client(config.oauth)
    logger.info("Authentication & Security Managers initialized.")
    return {
        "auth_manager": auth_manager,
        "oauth": oauth,
        "limiter": limiter,
        "auth": auth,
        "api_key_service": api_key_service,
        "guest_session_service": guest_session_service,
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
    capability_catalog = CapabilityCatalog()
    authorization_service = AuthorizationService()
    agent_execution_id_factory = AgentExecutionIdFactory()
    agent_execution_supervisor = AgentExecutionSupervisor()
    task_budget_settings = config.agent.task_budget
    task_budget_limits = TaskBudgetLimits(
        max_total_executions=task_budget_settings.max_total_executions,
        max_active_executions=task_budget_settings.max_active_executions,
        max_active_branches=task_budget_settings.max_active_branches,
        max_parallel_agents=task_budget_settings.max_parallel_agents,
        max_total_tool_calls=task_budget_settings.max_total_tool_calls,
        max_total_inference_calls=(
            task_budget_settings.max_total_inference_calls
        ),
        max_total_tokens=task_budget_settings.max_total_tokens,
        max_total_cost_usd=task_budget_settings.max_total_cost_usd,
        max_delegation_depth=task_budget_settings.max_delegation_depth,
    )
    task_budget_policy = TaskBudgetPolicy(
        version=task_budget_settings.policy_version,
        deny_recursive_agent_cycle=(
            task_budget_settings.deny_recursive_agent_cycle
        ),
    )
    task_budget_service = TaskBudgetService(
        eventing_manager.uow_factory,
        default_limits=task_budget_limits,
        default_policy=task_budget_policy,
    )

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
        agent_execution_id_factory=agent_execution_id_factory,
        agent_execution_supervisor=agent_execution_supervisor,
        task_budget_service=task_budget_service,
        task_budget_policy=task_budget_policy,
        multi_agent_coordinator=MultiAgentCoordinator(
            agent_registry,
            durable_store=DurableAgentStore(eventing_manager.uow_factory),
            execution_supervisor=agent_execution_supervisor,
            execution_id_factory=agent_execution_id_factory,
            task_budget_service=task_budget_service,
        ),
        **(security_services or {}),
    )
    eventing_manager.set_dependency_container(container)
    await container.mcp_manager.start_health_checker()

    # 2. Tạo RuntimeKernel nhận container
    kernel = RuntimeKernel(eventing_manager, container)
    container.runtime_kernel = kernel

    # 3. Tạo các instance Runtimes và Bind vào Container trước khi Kernel Bootstrap
    connection_runtime = ConnectionRuntime()
    connection_runtime.registration_service = ClientCapabilityRegistrationService(
        capability_catalog,
        connection_runtime.registry,
    )
    capability_routing_policy = CapabilityRoutingPolicy(
        connection_availability=connection_runtime.registry,
    )

    async def publish_capability_invocation(event):
        await container.event_bus.publish(
            BaseEvent(
                event_id=event.event_id,
                event_name=event.event_name,
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload=event.model_dump(mode="json"),
            )
        )

    capability_invocation_lifecycle = CapabilityInvocationLifecycle(
        SqlCapabilityInvocationStore(eventing_manager.uow_factory),
        publish_capability_invocation,
    )

    runtimes = [
        ("event_runtime", EventRuntime()),
        ("context_runtime", ContextRuntime()),
        ("connection_runtime", connection_runtime),
        ("session_runtime", SessionRuntime()),
        ("workflow_runtime", WorkflowRuntime(agent_execution_id_factory)),
        (
            "capability_runtime",
            CapabilityRuntime(
                registry=capability_registry,
                authorization=authorization_service,
                catalog=capability_catalog,
                routing_policy=capability_routing_policy,
                connection_registry=connection_runtime.registry,
                realtime=connection_runtime.realtime,
                invocation_lifecycle=capability_invocation_lifecycle,
            ),
        ),
        ("provider_runtime", ProviderRuntime(cb_manager)),
    ]

    for runtime_id, runtime_instance in runtimes:
        container.bind_runtime(runtime_id, runtime_instance)
        kernel.register_runtime(runtime_instance)

    if container.capability_runtime and hasattr(container.capability_runtime, "registry"):
        container.tool_registry = ToolRegistry(container.capability_runtime.registry)
        tools_dir = Path(__file__).resolve().parents[2] / "tools" / "v1"
        loaded_tools = register_local_tools(
            container.capability_runtime, container.tool_registry, tools_dir
        )
        logger.info("Local tools discovered", tools=loaded_tools)

    # 4. Bootstrap Kernel (RuntimeContext tự động được khởi tạo bên trong)
    await kernel.bootstrap()

    # Establish canonical agent ports while preserving the legacy coordinator path.
    agent_tool_policy = RegistryAgentToolPolicy(
        agent_registry=container.agent_registry,
        capability_registry=container.capability_registry,
        authorization=container.authorization_service,
        capability_catalog=capability_catalog,
    )
    agent_execution_policy = DefaultAgentExecutionPolicy()
    container.agent_tool_policy = agent_tool_policy
    container.agent_execution_policy = agent_execution_policy
    container.context_assembler = DefaultAgentContextAssembler(
        DefaultAgentSystemPromptProvider(),
        RegistryAgentCapabilityResolver(
            agent_registry=container.agent_registry,
            capability_registry=container.capability_registry,
            capability_catalog=capability_catalog,
            tool_policy=agent_tool_policy,
        ),
        RegistryAgentSkillResolver(
            agent_registry=container.agent_registry,
            capability_catalog=capability_catalog,
        ),
    )
    container.context_builder_port = ContextBuilderAdapter(
        container.context_runtime,
        container.capability_runtime,
        agent_tool_policy,
        context_assembler=container.context_assembler,
    )
    container.inference_port = ProviderInferenceAdapter(
        container.provider_runtime,
        container.http_client,
    )
    container.direct_chat_runtime = DirectChatRuntime(
        inference=container.inference_port,
        capability_runtime=container.capability_runtime,
    )
    container.tool_execution_port = CapabilityToolExecutionAdapter(
        container.capability_runtime,
        agent_tool_policy,
        agent_execution_policy,
    )
    container.tool_execution_port = AgentToolExecutionCoordinator(
        container.tool_execution_port,
    )
    container.agent_durable_store = DurableAgentStore(eventing_manager.uow_factory)
    container.continuation_service = AgentContinuationService(
        container.agent_durable_store
    )
    container.resume_planning_service = AgentResumePlanningService(
        container.agent_durable_store,
        container.capability_runtime,
    )
    container.agent_runtime = AgentRuntime(
        context_builder=container.context_builder_port,
        inference=container.inference_port,
        tool_execution=container.tool_execution_port,
        execution_policy=container.agent_execution_policy,
        durable_store=container.agent_durable_store,
        event_publisher=EventBusAgentEventPublisher(container.event_bus),
        continuation_service=container.continuation_service,
        task_budget_service=container.task_budget_service,
    )
    builtin_support = register_builtin_support(container)
    container.multi_agent_coordinator.agent_authorizer = (
        lambda identity, agent_id: (
            capability_catalog.contains_definition(agent_id)
            and container.authorization_service.is_allowed(
                identity, capability_catalog.get_definition(agent_id)
            )
        )
    )
    logger.info("Built-in agent support registered", support=builtin_support)

    # Cấu hình Multi-Agent Executor
    async def execute_registered_agent_task(
        task,
        *,
        identity,
        execution_id,
        correlation_id,
        parent_execution_id=None,
    ):
        agent = container.agent_registry.get(task.assigned_agent_id)
        if agent is None:
            raise LookupError(f"Agent '{task.assigned_agent_id}' is not registered.")
        # Agent context assembly reads canonical conversation history. A
        # multi-agent session has its own durable record, so ensure the paired
        # conversation record exists before entering AgentRuntime.
        async with container.uow_factory() as uow:
            conversation = await uow.sessions.get_by_id(task.session_id)
            if conversation is None:
                await uow.sessions.create_session(
                    user_id=identity.user_id,
                    organization_id=identity.organization_id,
                    session_id=task.session_id,
                )
                await uow.commit()
            elif conversation.user_id != identity.user_id:
                raise PermissionError(
                    f"Session '{task.session_id}' is not owned by this identity."
                )
        task_metadata = (
            dict(task.input.get("metadata", {}))
            if isinstance(task.input.get("metadata"), dict)
            else {}
        )
        if task.input.get("model"):
            task_metadata["model"] = task.input["model"]
        if task.client_id:
            task_metadata["client_id"] = task.client_id
        execution_context = AgentExecutionContext.create(
            execution_id=execution_id,
            agent_id=agent.name,
            session_id=task.session_id,
            correlation_id=correlation_id,
            identity=identity,
            limits=AgentExecutionLimits(),
            task_id=task.task_id,
            parent_execution_id=parent_execution_id,
            connection_id=task.connection_id,
            agent=agent,
            input=dict(task.input),
            metadata=task_metadata,
        )
        result = await container.agent_execution_supervisor.run(
            execution_context,
            lambda: container.agent_runtime.execute(execution_context),
        )
        return result.model_dump(mode="json")

    # Multi-agent HTTP tasks enter the canonical AgentRuntime loop.
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
    config = bootstrap_observability(app.state.bootstrap_config)
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

        if container.agent_execution_supervisor:
            await container.agent_execution_supervisor.quiesce()

        await eventing_manager.quiesce()

        if container.multi_agent_coordinator:
            await container.multi_agent_coordinator.shutdown()

        if container.agent_execution_supervisor:
            await container.agent_execution_supervisor.shutdown()

        if container.runtime_kernel:
            await container.runtime_kernel.shutdown()

        if container.mcp_manager:
            await container.mcp_manager.stop()

        await http_client.aclose()
        await storage_engine.disconnect()

        logger.info("Shutdown sequence completed cleanly.")


def create_app(config: ConfigSchema | None = None) -> FastAPI:
    """Tạo instance FastAPI và đăng ký Middlewares, Routers."""
    if config is None:
        config = ConfigManager().get_instance("se/config/default.yaml").initialize()
    app_instance = FastAPI(
        title=config.gateway.name,
        version=__version__,
        lifespan=lifespan,
    )
    app_instance.state.bootstrap_config = config

    # Middleware Stack
    create_middleware_stack(app_instance, config.auth)

    # Route Registrations: api/v1 is the sole HTTP router surface.
    app_instance.include_router(auth_router.router)
    app_instance.include_router(files_router.router)
    app_instance.include_router(models_router.router)
    app_instance.include_router(chat_router.router)
    app_instance.include_router(embeddings_router.router)
    app_instance.include_router(admin_router.router)
    app_instance.include_router(agent_router.router)
    app_instance.include_router(tool_router.router)
    app_instance.include_router(capability_router.router)
    app_instance.include_router(session_router.router)
    app_instance.include_router(events_router.router)
    app_instance.include_router(multi_agent_router.router)
    app_instance.include_router(health_router.router)

    return app_instance


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "se.src.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
