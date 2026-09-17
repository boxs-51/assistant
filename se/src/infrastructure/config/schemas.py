from pydantic import BaseModel, Field, AnyHttpUrl, model_validator, ConfigDict, SecretStr
from typing import Dict, Optional, Any
from ...version import __version__
# =================================================================
# CONFIGURATION SCHEMAS
# Đây là các Pydantic Model định nghĩa cấu trúc của cấu hình.
# Chúng chỉ dùng để xác thực, không tự động tải từ bất kỳ nguồn nào.
# =================================================================

class GatewaySettings(BaseModel):
    name: str = "AI Gateway"
    version: str = __version__
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    allowed_origins: list[str] = ["*"]

    @model_validator(mode="after")
    def enforce_canonical_version(self) -> "GatewaySettings":
        if self.version != __version__:
            raise ValueError(
                f"gateway.version is code-owned and must be {__version__}."
            )
        return self
class OAuthClientConfig(BaseModel):
    client_id: str
    client_secret: str

class OAuthSettings(BaseModel):
    google: Optional[OAuthClientConfig] = None
    github: Optional[OAuthClientConfig] = None

class AuthenticationSettings(BaseModel):
    enable: bool = True
    allow_guest: bool = False

    admin_ips: Dict[str, str] = {}
    public_paths: list[str] = [
        "/docs",
        "/openapi.json",
        "/health*",
        "/ready",
        "/metrics",
        "/stats",
        "/v1/auth/register/initiate",
        "/v1/auth/register/verify",
        "/v1/auth/password-reset/initiate",
        "/v1/auth/password-reset/confirm",
        "/v1/auth/login",
        "/v1/auth/refresh",
        "/v1/auth/logout",
        "/v1/auth/oauth/login/*",
        "/v1/auth/oauth/callback/*",
        "/v1/auth/guest",
    ]
    session_secret_key: SecretStr = SecretStr("")

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = 3600

    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    guest_token_ttl_seconds: int = 86400

    def validate_runtime_secrets(self) -> None:
        """Fail closed before authentication services are exposed."""
        jwt_secret = self.jwt_secret_key.get_secret_value()
        session_secret = self.session_secret_key.get_secret_value()
        insecure = {"", "change-me", "change-this-in-production"}

        def is_insecure(value: str) -> bool:
            return (
                value in insecure
                or value.startswith("replace-with-")
                or len(value.encode("utf-8")) < 32
            )

        if is_insecure(jwt_secret):
            raise ValueError("AUTH__JWT_SECRET_KEY must contain at least 32 bytes of non-default entropy.")
        if is_insecure(session_secret):
            raise ValueError("AUTH__SESSION_SECRET_KEY must contain at least 32 bytes of non-default entropy.")
        if jwt_secret == session_secret:
            raise ValueError("JWT and session secrets must be different.")

class FrontendSettings(BaseModel):
    oauth_callback_url: Optional[str] = None

class FillterSettings(BaseModel):
    enable_input_fillter: bool = True
    enable_output_fillter: bool = True

class RateLimitSettings(BaseModel):
    algorithm: str = "token_bucket"
    capacity: int = 100
    refill_rate: float = 5.0
    limit: int = 100
    window_size: int = 60
    cache_expire_seconds: int = 3600
    fail_mode: str = "open"  # 'open' hoặc 'closed'


class DriverConfig(BaseModel):
    enabled: bool = True
    required: bool = False

    options: Dict[str, Any] = Field(default_factory=dict)

class StorageSettings(BaseModel):
    drivers: Dict[str, DriverConfig] = Field(default_factory=dict)

class CircuitBreakerProviderSettings(BaseModel):
    """Cấu hình ngưỡng cho một Circuit Breaker cụ thể."""
    failure_threshold: int = 3
    reset_timeout: int = 10 # seconds
    success_threshold: int = 1

class CircuitBreakerSettings(BaseModel):
    default: CircuitBreakerProviderSettings = Field(default_factory=CircuitBreakerProviderSettings)
    providers: dict[str, CircuitBreakerProviderSettings] = Field(default_factory=dict)

class ProviderConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    base_url: Optional[AnyHttpUrl] = None

    options: Dict[str, Any] = Field(default_factory=dict)
    
class ProviderSettings(BaseModel):
    timeout: int = 60
    retry: int = 2
    enable_fallback: bool = True
    priority: list[str] = Field(default=["openai", "anthropic", "gemini", "ollama", "mock"])
    routing_rules_path: str = "config/routing/routing_rules.yaml"
    
    # Danh sách cấu hình chi tiết cho từng provider
    configs: Dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "openai": ProviderConfig(base_url="https://api.openai.com/v1"),
            "anthropic": ProviderConfig(base_url="https://api.anthropic.com"),
            "gemini": ProviderConfig(base_url="https://generativelanguage.googleapis.com"),
            "ollama": ProviderConfig(base_url="http://127.0.0.1:11434"),
            "mock": ProviderConfig(base_url="http://mock.invalid"),
        }
    )

class LoggingSettings(BaseModel):
    level: str = "INFO"
    file: str = "gateway.log"

class MetricsSettings(BaseModel):
    enable: bool = True
    port: int = 9090

class TracingSettings(BaseModel):
    enable: bool = False
    otlp_endpoint: str = "http://localhost:4318"

class SemanticCacheSettings(BaseModel):
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    embedding_cache_folder: str = "./data/embedding_models"

class TokenBudgetSettings(BaseModel):
    max_input_tokens: int = 32000
    max_output_tokens: int = 4096

class ConfigSchema(BaseModel):
    """
    Schema xác thực cuối cùng cho toàn bộ cấu hình ứng dụng.
    Đây là "Single Source of Truth" sau khi cấu hình đã được tải và hợp nhất.
    """
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    fillter: FillterSettings = Field(default_factory=FillterSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    circuit_breaker: CircuitBreakerSettings = Field(default_factory=CircuitBreakerSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    tracing: TracingSettings = Field(default_factory=TracingSettings)
    semantic_cache: SemanticCacheSettings = Field(default_factory=SemanticCacheSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    auth: AuthenticationSettings = Field(default_factory=AuthenticationSettings)
    oauth: OAuthSettings = Field(default_factory=OAuthSettings)
    frontend: FrontendSettings = Field(default_factory=FrontendSettings)

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    @model_validator(mode='after')
    def check_config_consistency(self) -> 'ConfigSchema':
        if self.provider.enable_fallback and not self.provider.priority:
            raise ValueError(
                "Provider 'priority' list cannot be empty when 'enable_fallback' is true."
            )
        return self
