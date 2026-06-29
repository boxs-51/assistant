from pydantic import BaseModel, Field, AnyHttpUrl, model_validator

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

class SecuritySettings(BaseModel):
    api_key: str = "change-me"
    enable_auth: bool = False
    enable_input_guardrail: bool = True
    enable_output_guardrail: bool = True

class RateLimitSettings(BaseModel):
    algorithm: str = "token_bucket"
    capacity: int = 100
    refill_rate: float = 5.0
    limit: int = 100
    window_size: int = 60

class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    cache_expire_seconds: int = 3600

class ProviderSettings(BaseModel):
    timeout: int = 60
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
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
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
    token_budget: TokenBudgetSettings = Field(default_factory=TokenBudgetSettings)

    class Config:
        extra = "forbid" # Ném lỗi nếu có key không xác định trong cấu hình

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
        if self.provider.priority:
            if "openai" in self.provider.priority and not self.openai.api_key:
                raise ValueError("OpenAI API key is required as it is in the provider priority list.")
            
            if "anthropic" in self.provider.priority and not self.anthropic.api_key:
                raise ValueError("Anthropic API key is required as it is in the provider priority list.")

            if "gemini" in self.provider.priority and not self.gemini.api_key:
                raise ValueError("Gemini API key is required as it is in the provider priority list.")

        return self