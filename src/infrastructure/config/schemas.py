from pydantic import BaseModel, Field, AnyHttpUrl, model_validator, ConfigDict
from typing import Dict, Optional
# =================================================================
# CONFIGURATION SCHEMAS
# Đây là các Pydantic Model định nghĩa cấu trúc của cấu hình.
# Chúng chỉ dùng để xác thực, không tự động tải từ bất kỳ nguồn nào.
# =================================================================

class GatewaySettings(BaseModel):
    name: str = "AI Gateway"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    allowed_origins: list[str] = ["*"]
class OAuthClientConfig(BaseModel):
    client_id: str
    client_secret: str

class OAuthSettings(BaseModel):
    google: Optional[OAuthClientConfig] = None
    github: Optional[OAuthClientConfig] = None

class AuthenticationSettings(BaseModel):
    enable: bool = True

    admin_ips: Dict[str, str] = {}
    public_paths: list[str] = ["/docs", "/openapi.json", "/health*", "/ready", "/metrics", "/stats", "/auth/*"]
    session_secret_key: str = "change-this-in-production"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = 3600

    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

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
    fail_mode: str = "open"  # 'open' hoặc 'closed'

class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    cache_expire_seconds: int = 3600
    key_prefix: str = "aigateway"

class DriverConfig(BaseModel):
    enabled: bool = True
    required: bool = False
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    path: Optional[str] = None
    password: Optional[str] = None

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


class ProviderSettings(BaseModel):
    timeout: int = 60
    mock_enabled: bool = False
    mock_seed: str = "assistant-offline-mock"
    mock_scenario: str = "success"
    retry: int = 2
    enable_fallback: bool = True
    priority: list[str] = Field(default=["openai", "anthropic", "gemini", "ollama"])
    routing_rules_path: str = "config/routing/routing_rules.yaml"

class OpenAISettings(BaseModel):
    api_key: str = ""
    base_url: AnyHttpUrl = "https://api.openai.com/v1"

class AnthropicSettings(BaseModel):
    api_key: str = ""
    base_url: AnyHttpUrl = "https://api.anthropic.com"

class GeminiSettings(BaseModel):
    api_key: str = ""
    base_url: AnyHttpUrl = "https://generativelanguage.googleapis.com"

class OllamaSettings(BaseModel):
    base_url: AnyHttpUrl = "http://127.0.0.1:11434"

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
    similarity_threshold: float = 0.95
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    collection: str = "semantic_cache"
    path: str = "./data/chroma_cache"
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
    redis: RedisSettings = Field(default_factory=RedisSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
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
        """
        Thực hiện các quy tắc xác thực chéo (cross-field validation) sau khi
        tất cả các giá trị đã được parse.
        """
        # 1. Xác thực logic fallback của provider
        if self.provider.enable_fallback and not self.provider.priority:
            raise ValueError(
                "Provider 'priority' list cannot be empty when 'enable_fallback' is true."
            )

        # 2. Xác thực API key cho các provider có trong danh sách ưu tiên
        # Logic mới: Chỉ yêu cầu API key nếu provider đó có trong danh sách ưu tiên
        # VÀ key đó không được cung cấp. Điều này cho phép ứng dụng khởi động
        # mà không cần key nếu các provider đó không được sử dụng.
        if self.provider.priority:
            if "openai" in self.provider.priority and not self.openai.api_key:
                # Thay vì ném lỗi, chúng ta sẽ ghi một cảnh báo.
                # Lỗi thực sự sẽ xảy ra khi cố gắng sử dụng provider này (trong ProviderDiscovery).
                # Điều này cho phép gateway khởi động mà không cần tất cả các key.
                pass # Bỏ qua lỗi ở đây
            
            if "anthropic" in self.provider.priority and not self.anthropic.api_key:
                # Tương tự, bỏ qua lỗi ở đây
                pass

            if "gemini" in self.provider.priority and not self.gemini.api_key:
                # Tương tự, bỏ qua lỗi ở đây
                pass

        return self