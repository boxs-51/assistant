# e:\assistant\src	ransport\gatewayuntime.py

import httpx
import structlog
from fastapi import FastAPI

from ...runtime.kernel.runtime import Runtime
from ...infrastructure.config import settings
from ...infrastructure.config.core import ConfigLoader, ConfigurationRegistry
from ...infrastructure.storage.core.manager import StorageEngine
from ...infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ...context.manager import ContextEngine
from ...event_bus.manager import EventingManager
from ...provider import ModelRouter
from ...tool import GatewayToolManager
from ...tool.registry import ToolRegistry
from ...tool.executor import ExecutorRegistry, LocalExecutor, McpExecutor, NativeExecutor, WorkflowExecutor
from ...tool._mcp.mcp_manager import GatewayMcpManager
from ...runtime.capability.registry import CapabilityRegistry
from ...runtime.capability.dispatcher import CapabilityDispatcher
from ...runtime.capability.drivers.tool_driver import ToolDriver
from ...agent.registry import AgentRegistry
from ...domain.schemas.enums import ToolType
from .limiter import RateLimiterManager
from .circuit_breaker import CircuitBreakerManager
from .authentication.manager import AuthenticationManager
from .authentication.services.api_key_service import APIKeyService
from .authentication.services.token_service import TokenService
from .authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .authentication.authenticators.jwt_authenticator import JWTAuthenticator
from .authentication.oauth import create_oauth_client
from .fillter import InputFillter, OutputFillter
from .fillter.guar import GuardrailSystem

logger = structlog.get_logger(__name__)

class GatewayRuntime(Runtime):
    """
    A runtime that encapsulates the entire legacy Gateway initialization logic.
    This acts as an adapter to make the existing gateway components manageable
    by the new Runtime Kernel.
    """
    def __init__(self, app: FastAPI):
        self.app = app
        # Service attributes
        self.storage: StorageEngine
        self.http_client: httpx.AsyncClient
        self.agent_registry: AgentRegistry
        self.tool_registry: ToolRegistry
        self.context_manager: ContextEngine
        self.eventing_manager: EventingManager
        self.event_bus: EventBus
        self.limiter: RateLimiterManager
        self.auth_manager: AuthenticationManager
        self.tool_manager: GatewayToolManager
        self.capability_dispatcher: CapabilityDispatcher
        self.oauth: dict
        self.router: ModelRouter
        self.input_fillter: InputFillter
        self.output_fillter: OutputFillter


    async def initialize(self, **kwargs) -> None:
        logger.info("GatewayRuntime initializing...")
        
        # Most of the logic from main.py's startup_event is moved here.
        
        # --- Storage Engine Initialization ---
        self.storage = StorageEngine()
        await self.storage.connect()
        self.app.state.storage = self.storage
        logger.info("Storage Engine connected.")
        
        self.app.state.cache = self.storage.services.get("semantic_cache")
        
        # --- Agent & Tool Registries ---
        self.agent_registry = AgentRegistry()
        self.tool_registry = ToolRegistry()
        self.app.state.agent_registry = self.agent_registry
        self.app.state.tool_registry = self.tool_registry
        logger.info("Agent and Tool registries initialized.")

        # --- ContextEngine ---
        db_driver = self.storage.drivers.get("sqlite")
        uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
        self.context_manager = ContextEngine(self.storage, uow_factory)
        self.app.state.context_manager = self.context_manager
        logger.info("Context Session Manager initialized.")

        # --- Event Bus ---
        self.eventing_manager = EventingManager(storage_engine=self.storage, context_engine=self.context_manager)
        self.eventing_manager.register_subscribers()
        self.event_bus = self.eventing_manager.bus
        self.app.state.eventing_manager = self.eventing_manager
        self.app.state.event_bus = self.event_bus
        logger.info("Eventing System initialized.")

        # --- Centralized Managers ---
        circuit_breaker_manager = CircuitBreakerManager()
        self.limiter = RateLimiterManager(
            cache_driver=self.storage.drivers.get("redis")._client,
            circuit_breaker_manager=circuit_breaker_manager
        )
        self.app.state.limiter = self.limiter

        # --- Authentication Manager ---
        session_repo = self.storage.repositories.get("sessions")
        token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo)
        api_key_service = APIKeyService(uow_factory=uow_factory)
        api_key_authenticator = APIKeyAuthenticator(api_key_service)
        jwt_authenticator = JWTAuthenticator(token_service, uow_factory)
        self.auth_manager = AuthenticationManager(
            authenticators=[api_key_authenticator, jwt_authenticator]
        )
        self.app.state.auth_manager = self.auth_manager
        logger.info("Authentication Manager initialized.")

        # --- Tool Runtime (Legacy) ---
        mcp_manager = GatewayMcpManager()
        executor_registry = ExecutorRegistry()
        executor_registry.register(ToolType.LOCAL, LocalExecutor())
        executor_registry.register(ToolType.MCP, McpExecutor(mcp_manager))
        executor_registry.register(ToolType.NATIVE, NativeExecutor())
        workflow_executor = WorkflowExecutor(executor_registry, self.tool_registry)
        executor_registry.register(ToolType.WORKFLOW, workflow_executor)
        self.tool_manager = GatewayToolManager(self.tool_registry, executor_registry, self.event_bus)
        self.app.state.tool_manager = self.tool_manager
        logger.info("Tool Runtime (Manager & Executors) initialized.")

        # --- Capability Runtime (Phase 4) ---
        capability_registry = CapabilityRegistry()
        tool_driver = ToolDriver(self.tool_manager)
        all_tools = self.tool_registry.get_all()
        for tool_def in all_tools:
            capability_registry.register(tool_def.name, tool_driver)
        self.capability_dispatcher = CapabilityDispatcher(capability_registry)
        self.app.state.capability_dispatcher = self.capability_dispatcher
        logger.info("Capability Runtime initialized.")

        # --- Other Services ---
        self.oauth = create_oauth_client()
        self.router = ModelRouter(circuit_breaker_manager=circuit_breaker_manager)
        guardrail_system = GuardrailSystem()
        self.input_fillter = InputFillter(guardrail_system)
        self.output_fillter = OutputFillter(guardrail_system)
        
        self.app.state.oauth = self.oauth
        self.app.state.router = self.router
        self.app.state.input_fillter = self.input_fillter
        self.app.state.output_fillter = self.output_fillter
        
        # HTTP Client
        self.http_client = httpx.AsyncClient(timeout=settings.provider.timeout)
        self.app.state.http_client = self.http_client

        logger.info("GatewayRuntime initialized successfully.")

    async def start(self) -> None:
        logger.info("GatewayRuntime started.")
        pass

    async def stop(self) -> None:
        logger.info("GatewayRuntime stopping...")
        if hasattr(self, 'http_client') and self.http_client:
            await self.http_client.aclose()
        if hasattr(self, 'storage') and self.storage:
            await self.storage.disconnect()
        logger.info("GatewayRuntime stopped.")

    async def dispose(self) -> None:
        logger.info("GatewayRuntime disposed.")
        pass
