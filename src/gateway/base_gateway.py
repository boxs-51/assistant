from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware
import httpx
import structlog
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.middleware.sessions import SessionMiddleware


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
from .authentication.authenticators.jwt_authenticator import JWTAuthenticator
from .storage.core.unit_of_work import SqlAlchemyUnitOfWork

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
        "/docs", "/openapi.json", "/health*", "/ready", "/metrics", "/stats",
        "/auth/*"  # Sử dụng wildcard để mở tất cả các endpoint dưới /auth
    ]
)
SECRET_KEY="09d25e094faa6ca2556c818166b7a9563"
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY, # Đảm bảo đã định nghĩa SECRET_KEY trong file config/env của bạn
    session_cookie="oauth_session", # Tùy chọn đặt tên cookie
    max_age=600 # Session chỉ cần sống khoảng 10 phút để phục vụ luồng login
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
    app.state.cache = app.state.storage.services.get("semantic_cache")
    
    # --- Centralized Managers ---
    # CircuitBreakerManager giờ được dùng chung cho cả Router và Rate Limiter
    circuit_breaker_manager = CircuitBreakerManager()
    app.state.limiter = RateLimiterManager(
        cache_driver=app.state.storage.drivers.get("redis")._client,
        circuit_breaker_manager=circuit_breaker_manager)

    # --- Authentication Manager Initialization ---
    # 1. Tạo các dependency cần thiết cho services
    db_driver = storage_engine.drivers.get("sqlite")
    uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver)
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