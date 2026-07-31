from contextlib import asynccontextmanager
from typing import Dict, Any, Tuple
from fastapi import FastAPI
import httpx
import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# Config & Observability
from .infrastructure.config import settings
from .infrastructure.config.core import ConfigLoader, ConfigurationRegistry
from .infrastructure.observability import ObservabilityConfig, LoggingConfig, TracingConfig

from .transport.gateway.middleware.metris import setup_gateway_observability
from .transport.gateway.middleware.factory import create_middleware_stack

# Storage & UoW
from .infrastructure.storage.core.manager import StorageEngine
from .infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork

# Security & Gateway Infrastructure
from .transport.gateway.limiter import RateLimiterManager
from .transport.gateway.circuit_breaker import CircuitBreakerManager
from .transport.gateway.authentication.oauth import create_oauth_client
from .transport.gateway.authentication.manager import AuthenticationManager
from .transport.gateway.authentication.services.api_key_service import APIKeyService
from .transport.gateway.authentication.services.token_service import TokenService
from .transport.gateway.authentication.authenticators.api_key_authenticator import APIKeyAuthenticator
from .transport.gateway.authentication.authenticators.jwt_authenticator import JWTAuthenticator

from .infrastructure.event_bus.bus import EventBus
# Kernel & Runtimes
from src.kernel.lifecycle import RuntimeRegistry, LifecycleManager

from src.runtimes.connection.runtime import ConnectionRuntime
from src.runtimes.session.runtime import SessionRuntime
from src.runtimes.workflow.runtime import WorkflowRuntime
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.provider.runtime import ProviderRuntime
from src.runtimes.event.runtime import EventRuntime

# Routers
from .transport.gateway.router.auth import router as auth_router
from .transport.gateway.router.agent import router as agent_router
from .transport.gateway.router.tool import router as tool_router
from .transport.gateway.router.events import router as events_router
from .transport.gateway.router.admin import router as admin_router

from .transport.gateway.http import (
    chat_router, embeddings_router, 
    files_router, health_router,
    models_router
)


logger = structlog.get_logger(__name__)


# ==============================================================================
# BOOTSTRAP FACTORIES
# ==============================================================================

def bootstrap_observability(app: FastAPI) -> None:
    """Tải cấu hình gateway và kích hoạt hệ thống Observability (Metrics & Tracing)."""
    loader = ConfigLoader(default_config_path="config/gateway/default.yaml")
    app_config = loader.load_config()
    ConfigurationRegistry.set_config(app_config)
    app.state.config = ConfigurationRegistry.get_config()

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
    FastAPIInstrumentor.instrument_app(app)


async def bootstrap_storage(app: FastAPI) -> Tuple[StorageEngine, Any]:
    """Kết nối cơ sở dữ liệu và tạo Unit of Work Factory."""
    storage_engine = StorageEngine()
    await storage_engine.connect()
    app.state.storage = storage_engine
    
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
    
    logger.info("Storage Engine connected successfully.")
    return storage_engine, uow_factory


def bootstrap_security(app: FastAPI, storage_engine: StorageEngine, uow_factory: Any, cb_manager: CircuitBreakerManager) -> None:
    """Khởi tạo các dịch vụ Xác thực, OAuth và Rate Limiting."""
    redis_client = storage_engine.drivers.get("redis")._client
    
    app.state.limiter = RateLimiterManager(
        cache_driver=redis_client,
        circuit_breaker_manager=cb_manager
    )

    session_repo = storage_engine.repositories.get("sessions")
    token_service = TokenService(uow_factory=uow_factory, session_repo=session_repo)
    api_key_service = APIKeyService(uow_factory=uow_factory)

    app.state.auth_manager = AuthenticationManager(
        authenticators=[
            APIKeyAuthenticator(api_key_service),
            JWTAuthenticator(token_service, uow_factory),
        ]
    )
    app.state.oauth = create_oauth_client()
    logger.info("Authentication & Security Managers initialized.")


async def bootstrap_runtime_kernel(
    app: FastAPI,
    storage_engine: StorageEngine,
    uow_factory: Any,
    http_client: httpx.AsyncClient,
    cb_manager: CircuitBreakerManager,
) -> LifecycleManager:
    """Khởi tạo EventBus, các Runtimes và kích hoạt Boot Sequence cho Kernel."""
    event_bus = EventBus()
    registry = RuntimeRegistry()
    lifecycle_manager = LifecycleManager(registry, event_bus)

    # Đăng ký Runtimes
    registry.register(ConnectionRuntime(event_bus))
    registry.register(SessionRuntime(event_bus))
    registry.register(WorkflowRuntime(event_bus))
    registry.register(CapabilityRuntime(event_bus))
    registry.register(ProviderRuntime(event_bus, cb_manager))
    registry.register(EventRuntime(event_bus))

    # Thiết lập thứ tự Boot
    boot_order = [
        "EventRuntime",
        "ConnectionRuntime",
        "CapabilityRuntime",
        "ProviderRuntime",
        "ContextRuntime",
        "SessionRuntime",
        "WorkflowRuntime",
    ]
    lifecycle_manager.set_boot_order(boot_order)

    # Boot Kernel với Global Context
    global_context = {
        "storage_engine": storage_engine,
        "uow_factory": uow_factory,
        "http_client": http_client,
    }
    
    app.state.event_bus = event_bus
    await lifecycle_manager.boot_sequence(global_context=global_context)
    
    logger.info("AI Runtime Kernel & Runtimes booted successfully.")
    return lifecycle_manager


# ==============================================================================
# APPLICATION LIFESPAN & CREATION
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý tập trung toàn bộ vòng đời ứng dụng (Startup & Graceful Shutdown)."""
    logger.info("Starting AI Gateway Application...")

    # Shared Instances
    cb_manager = CircuitBreakerManager()
    http_client = httpx.AsyncClient(timeout=settings.provider.timeout)
    app.state.http_client = http_client

    # 1. Startup Sequence
    bootstrap_observability(app)
    storage_engine, uow_factory = await bootstrap_storage(app)
    bootstrap_security(app, storage_engine, uow_factory, cb_manager)
    lifecycle_manager = await bootstrap_runtime_kernel(
        app, storage_engine, uow_factory, http_client, cb_manager
    )

    yield  # --- APPLICATION IS RUNNING AND SERVING TRAFFIC ---

    # 2. Shutdown Sequence
    logger.info("Initiating Application Shutdown sequence...")

    await lifecycle_manager.shutdown_sequence()

    if hasattr(app.state, "http_client"):
        await app.state.http_client.aclose()

    if hasattr(app.state, "storage"):
        await app.state.storage.disconnect()

    logger.info("Shutdown sequence completed cleanly.")


def create_app() -> FastAPI:
    """Tạo instance FastAPI và đăng ký Middlewares, Routers."""
    app_instance = FastAPI(title="AI Gateway", lifespan=lifespan)

    # Middleware Stack
    create_middleware_stack(app_instance)

    # Route Registrations
    app_instance.include_router(auth_router)
    app_instance.include_router(files_router.router)
    app_instance.include_router(models_router.router)
    app_instance.include_router(chat_router.router)
    app_instance.include_router(embeddings_router.router)
    app_instance.include_router(admin_router)
    app_instance.include_router(agent_router)
    app_instance.include_router(tool_router)
    app_instance.include_router(events_router)
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