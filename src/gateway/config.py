from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Global configuration for AI Gateway.
    Automatically loads from .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # =====================================================
    # Gateway
    # =====================================================

    GATEWAY_NAME: str = "AI Gateway"
    GATEWAY_VERSION: str = "1.0.0"

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DEBUG: bool = False

    # =====================================================
    # Security
    # =====================================================

    API_KEY: str = "change-me"

    ENABLE_AUTH: bool = False

    ENABLE_INPUT_GUARDRAIL: bool = True
    ENABLE_OUTPUT_GUARDRAIL: bool = True

    # =====================================================
    # Rate Limit
    # =====================================================

    RATE_LIMIT_ALGORITHM: str = "token_bucket" # "token_bucket" hoặc "sliding_window"

    # Cấu hình cho Token Bucket
    RATE_LIMIT_CAPACITY: int = 100
    RATE_LIMIT_REFILL_RATE: float = 5.0

    # Cấu hình cho Sliding Window
    RATE_LIMIT_LIMIT: int = 100 # Số request tối đa
    RATE_LIMIT_WINDOW_SIZE: int = 60 # Kích thước cửa sổ (giây)

    # =====================================================
    # Redis
    # =====================================================

    REDIS_URL: str = "redis://localhost:6379/0"

    CACHE_EXPIRE_SECONDS: int = 3600

    # =====================================================
    # Provider
    # =====================================================

    PROVIDER_TIMEOUT: int = 60

    PROVIDER_RETRY: int = 2

    ENABLE_PROVIDER_FALLBACK: bool = True

    # Thứ tự ưu tiên của các provider cho chuỗi fallback mặc định.
    # Các provider sẽ được thử theo thứ tự này.
    # Provider không được cấu hình (thiếu API key/URL) sẽ tự động bị bỏ qua.
    PROVIDER_PRIORITY: list[str] = Field(default=["openai", "anthropic", "gemini", "ollama"])

    # Đường dẫn đến file YAML chứa các quy tắc định tuyến.
    ROUTING_RULES_PATH: str = "config/routing/routing_rules.yaml"

    # =====================================================
    # OpenAI
    # =====================================================

    OPENAI_API_KEY: str = ""

    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # =====================================================
    # Anthropic
    # =====================================================

    ANTHROPIC_API_KEY: str = ""

    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"

    # =====================================================
    # Gemini
    # =====================================================

    GEMINI_API_KEY: str = ""

    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com"

    # =====================================================
    # Ollama
    # =====================================================

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"

    # =====================================================
    # Logging
    # =====================================================

    LOG_LEVEL: str = "INFO"

    LOG_FILE: str = "gateway.log"

    # =====================================================
    # Metrics
    # =====================================================

    ENABLE_METRICS: bool = True

    METRICS_PORT: int = 9090

    # =====================================================
    # Tracing (OpenTelemetry)
    # =====================================================

    ENABLE_TRACING: bool = False

    # Endpoint của OTLP Collector (ví dụ: Jaeger, Grafana Tempo)
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4318"

    # =====================================================
    # Semantic Cache
    # =====================================================

    CACHE_SIMILARITY_THRESHOLD: float = 0.95

    # REFACTORED: Changed to a valid SentenceTransformer model from Hugging Face Hub.
    # 'text-embedding-3-small' is an OpenAI model name, not compatible with this library.
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    EMBEDDING_DEVICE: str = "cpu"

    CACHE_COLLECTION: str = "semantic_cache"

    CACHE_PATH: str = "./data/chroma_cache"

    EMBEDDING_CACHE_FOLDER: str = "./data/embedding_models"

    # =====================================================
    # Token Budget
    # =====================================================

    MAX_INPUT_TOKENS: int = 32000

    MAX_OUTPUT_TOKENS: int = 4096


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()