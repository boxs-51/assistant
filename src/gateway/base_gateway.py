from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware
import httpx
from .event_bus.bus import EventBus
from .event_bus.manager import EventingManager # Đổi tên thành EventingManager
from .context.manager import ContextEngine # Đổi tên thành ContextEngine
import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


from .config.core import ConfigLoader, ConfigurationRegistry
from .limiter import RateLimiterManager
from .routing import ModelRouter
from .circuit_breaker import CircuitBreakerManager
from .authentication.oauth import create_oauth_client
from .authentication.manager import AuthenticationManager
from .router.auth import router as auth_router
from .authentication.services.api_key_service import APIKeyService
from .authentication.services.token_service import TokenService
from .authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .tool.executor import ExecutorRegistry, LocalExecutor, McpExecutor, NativeExecutor, WorkflowExecutor
from .tool import GatewayToolManager
from .tool._mcp.mcp_manager import GatewayMcpManager
from .authentication.authenticators.jwt_authenticator import JWTAuthenticator
from .storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .middleware.factory import create_middleware_stack
from .middleware.observability import observability_middleware

from .storage.core.manager import StorageEngine
from .router.files import router as files_router
from .router.models import router as models_router
from .router.chat import router as chat_router
from .fillter import InputFillter, OutputFillter
from ..guardrail.guar import GuardrailSystem
from .agent.registry import AgentRegistry
from .router.agent import router as agent_router
from .tool.registry import ToolRegistry
from .router.tool import router as tool_router
from .router.events import router as events_router
from .router.embeddings import router as embeddings_router
from .router.admin import router as admin_router
from .router.health import router as health_router
from .config import settings
from .schemas.enums import ToolType

from shared_core.observability import ObservabilityConfig, LoggingConfig, TracingConfig
from .middleware.observability import gateway_metrics

app = FastAPI(title="AI Gateway")
logger = structlog.get_logger(__name__)

create_middleware_stack(app)

# Import các router từ các module
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(embeddings_router)
app.include_router(admin_router)
app.include_router(agent_router)
app.include_router(tool_router)
app.include_router(events_router)
app.include_router(health_router)

@app.on_event("startup")
async def startup_event():
    """Khởi tạo các kết nối cần thiết khi server khởi động."""
    # 1. Tải cấu hình

    loader = ConfigLoader(default_config_path="config/gateway/default.yaml")
    app_config = loader.load_config()
    ConfigurationRegistry.set_config(app_config)
    app.state.config = ConfigurationRegistry.get_config()

    # Cấu hình logging ngay khi khởi động
    config = ObservabilityConfig(service_name=settings.gateway.name,
                                 service_version=settings.gateway.version,
                                 logging=LoggingConfig(level=settings.logging.level),
                                 tracing=TracingConfig(enable=settings.tracing.enable, otlp_endpoint=settings.tracing.otlp_endpoint)
                                )
    gateway_metrics.setup_gateway_observability(config)
    # Tự động instrument FastAPI app
    FastAPIInstrumentor.instrument_app(app)

    # --- Storage Engine Initialization (Chapter 1) ---
    # Khởi tạo StorageEngine để quản lý tất cả các kết nối (DB, Cache, Vector, Object Storage)
    storage_engine = StorageEngine()
    await storage_engine.connect()
    app.state.storage = storage_engine
    logger.info("Storage Engine connected.")
    # -------------------------------------------------
    app.state.cache = app.state.storage.services.get("semantic_cache")
    
    # --- Agent & Tool Registries ---
    # Khởi tạo các registry để quản lý định nghĩa
    app.state.agent_registry = AgentRegistry()
    app.state.tool_registry = ToolRegistry()
    logger.info("Agent and Tool registries initialized.")



    # ContextEngine giờ đây sử dụng UoW Factory để truy cập DB
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
    app.state.context_manager = ContextEngine(storage_engine, uow_factory)
    logger.info("Context Session Manager initialized.")

    # --- Event Bus & Context Manager Initialization ---
    eventing_manager = EventingManager(storage_engine=storage_engine,context_engine=app.state.context_manager)
    eventing_manager.register_subscribers()
    app.state.eventing_manager = eventing_manager
    # Cung cấp các thành phần con để các module khác có thể truy cập trực tiếp
    app.state.event_bus = eventing_manager.bus
    logger.info("Eventing System (Manager, Bus, Registry, Subscribers) initialized.")

    # --- Centralized Managers ---
    # CircuitBreakerManager giờ được dùng chung cho cả Router và Rate Limiter
    circuit_breaker_manager = CircuitBreakerManager()
    app.state.limiter = RateLimiterManager(
        cache_driver=app.state.storage.drivers.get("redis")._client,
        circuit_breaker_manager=circuit_breaker_manager)

    # --- Authentication Manager Initialization ---
    # 1. Tạo các dependency cần thiết cho services
    db_driver = storage_engine.drivers.get("sqlite")
    session_repo = storage_engine.repositories.get("sessions")

    # 2. Khởi tạo các service mà AuthenticationManager cần
    token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    # 3. Khởi tạo các chiến lược xác thực (Authenticators)
    api_key_authenticator = APIKeyAuthenticator(api_key_service)
    jwt_authenticator = JWTAuthenticator(token_service, uow_factory)

    # 4. Khởi tạo AuthenticationManager với danh sách các chiến lược
    # Thứ tự trong danh sách này rất quan trọng, nó quyết định độ ưu tiên xác thực.
    app.state.auth_manager = AuthenticationManager(
        authenticators=[api_key_authenticator, jwt_authenticator]
    )
    logger.info("Authentication Manager initialized.")

    # --- Tool Runtime Initialization ---
    # 1. Khởi tạo các executor cụ thể
    mcp_manager = GatewayMcpManager() # Giả sử đã có
    local_executor = LocalExecutor()
    mcp_executor = McpExecutor(mcp_manager)
    native_executor = NativeExecutor()

    # 2. Khởi tạo Registry và đăng ký các executor
    executor_registry = ExecutorRegistry()
    executor_registry.register(ToolType.LOCAL, local_executor)
    executor_registry.register(ToolType.MCP, mcp_executor)
    executor_registry.register(ToolType.NATIVE, native_executor)

    # 3. WorkflowExecutor cần chính ExecutorRegistry để hoạt động, inject nó vào
    workflow_executor = WorkflowExecutor(executor_registry, app.state.tool_registry)
    executor_registry.register(ToolType.WORKFLOW, workflow_executor)

    # 4. Khởi tạo ToolManager với các registry đã hoàn chỉnh
    app.state.tool_manager = GatewayToolManager(app.state.tool_registry, executor_registry, app.state.event_bus)
    logger.info("Tool Runtime (Manager & Executors) initialized.")

    # --- OAuth Client Initialization ---
    app.state.oauth = create_oauth_client()
    logger.info("OAuth clients initialized.")

    app.state.router = ModelRouter(
        circuit_breaker_manager=circuit_breaker_manager # Inject vào router
    )
    # -------------------------------------
    # --- New Guardrail Initialization ---
    guardrail_system = GuardrailSystem()
    app.state.input_fillter = InputFillter(guardrail_system)
    app.state.output_fillter = OutputFillter(guardrail_system)
    # ----------------------------------
    app.state.http_client = httpx.AsyncClient(timeout=settings.provider.timeout) # type: ignore
    logger.info("Gateway startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    """Đóng các kết nối khi server tắt."""
    if hasattr(app.state, 'storage'):
        await app.state.storage.disconnect()
    # await app.state.redis_connection.close() # [REMOVED] Không cần thiết nữa vì StorageEngine đã quản lý
    await app.state.http_client.aclose()

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.gateway.base_gateway:app", 
        host="127.0.0.1", 
        port=8000, 
        reload=True
    )