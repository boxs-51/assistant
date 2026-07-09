from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware
import httpx
import structlog
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor



from .config.core import ConfigLoader, ConfigurationRegistry
from .limiter import RateLimiterManager
from .routing import ModelRouter
from .circuit_breaker import CircuitBreakerManager
from .authentication.oauth import create_oauth_client
from .authentication.manager import AuthenticationManager
from .router.auth import router as auth_router
from .authentication.middleware import AuthenticationMiddleware
from .middleware.observability import observability_middleware

from .storage.core.manager import StorageEngine
from .router.files import router as files_router
from .router.models import router as models_router
from .router.chat import router as chat_router
from .fillter import InputFillter, OutputFillter
from ..guardrail.guar import GuardrailSystem
from .router.embeddings import router as embeddings_router
from .router.admin import router as admin_router
from .router.health import router as health_router
from .config import settings

from shared_core.observability import ObservabilityConfig ,LoggingConfig, TracingConfig
from .middleware.observability import gateway_metrics
from .config import settings

app = FastAPI(title="AI Gateway")
tracer = trace.get_tracer(__name__)
logger = structlog.get_logger(__name__)

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5500",
    "*",
]

# Thêm middleware xác thực mới
# Middleware này phải được thêm TRƯỚC CORSMiddleware để nó không can thiệp vào các request OPTIONS của CORS.
app.add_middleware(
    AuthenticationMiddleware,
    public_paths=[
        "/docs", "/openapi.json", "/health", "/ready", "/metrics", "/stats",
        # Chỉ các endpoint cụ thể trong /auth là public
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/oauth"
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Cho phép tất cả các phương thức bao gồm OPTIONS, GET, POST, DELETE
    allow_headers=["*"],  # Cho phép tất cả các headers (như Authorization)
)

app.middleware("http")(observability_middleware)

# Import các router từ các module
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(embeddings_router)
app.include_router(admin_router)
app.include_router(health_router)

@app.on_event("startup")
async def startup_event():
    """Khởi tạo các kết nối cần thiết khi server khởi động."""
    # 1. Tải cấu hình

    loader = ConfigLoader(default_config_path="config/gateway/default.yaml")
    app_config = loader.load_config()
    ConfigurationRegistry.set_config(app_config)
    #settings = ConfigurationRegistry.get_config()
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
    app.state.cache = app.state.storage.services("semantic_cache")
    
    # --- Centralized Managers ---
    # CircuitBreakerManager giờ được dùng chung cho cả Router và Rate Limiter
    circuit_breaker_manager = CircuitBreakerManager()
    app.state.limiter = RateLimiterManager(
        cache_driver=app.state.storage.drivers.get("redis")._client,
        circuit_breaker_manager=circuit_breaker_manager)

    # --- Authentication Manager Initialization ---
    app.state.auth_manager = AuthenticationManager(
        user_repo=storage_engine.repositories.get("users"),
        api_key_repo=storage_engine.repositories.get("api_keys"),
        session_repo=storage_engine.repositories.get("sessions"))
    logger.info("Authentication Manager initialized.")

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
    app.state.tracer = trace.get_tracer(__name__)
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