
#metrics.py
from prometheus_client import Counter, Histogram, Gauge

# =================================================================
# METRICS DEFINITION
# Định nghĩa các số liệu theo chuẩn Prometheus.
# =================================================================

# --- Request Metrics ---
REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Tổng số request đi vào gateway.",
    ["method", "endpoint"]
)
REQUESTS_IN_FLIGHT = Gauge(
    "gateway_requests_in_flight",
    "Số request đang được xử lý đồng thời."
)
# Có thể thêm một Histogram cho request latency tổng thể ở đây
REQUESTS_SUCCESS_TOTAL = Counter(
    "gateway_requests_success_total",
    "Tổng số request xử lý thành công."
)
REQUESTS_FAILED_TOTAL = Counter(
    "gateway_requests_failed_total",
    "Tổng số request xử lý thất bại.",
    ["error_code"]
)

# --- Cache Metrics ---
CACHE_HITS_TOTAL = Counter(
    "gateway_cache_hits_total",
    "Tổng số lần tìm thấy trong cache (cache hit)."
)
CACHE_MISSES_TOTAL = Counter(
    "gateway_cache_misses_total",
    "Tổng số lần không tìm thấy trong cache (cache miss).",
    ["reason"] # e.g., 'not_found', 'expired', 'below_threshold'
)

CACHE_WRITE_TOTAL = Counter(
    "gateway_cache_write_total",
    "Tổng số lần ghi vào cache."
)

SEMANTIC_CACHE_LATENCY_SECONDS = Histogram(
    "gateway_semantic_cache_latency_seconds",
    "Phân phối độ trễ của các hoạt động cache ngữ nghĩa.",
    ["operation"] # 'get' hoặc 'set'
)
# --- Security Metrics ---
BLOCKED_REQUESTS_TOTAL = Counter(
    "gateway_blocked_requests_total",
    "Tổng số request bị chặn bởi các lớp bảo vệ.",
    ["reason"]  # e.g., 'rate_limit', 'prompt_injection'
)

# --- Provider Metrics ---
PROVIDER_ERRORS_TOTAL = Counter(
    "gateway_provider_errors_total",
    "Tổng số lỗi từ các LLM provider.",
    ["provider", "error_code"]
)
CIRCUIT_BREAKER_OPENS_TOTAL = Counter(
    "gateway_circuit_breaker_opens_total",
    "Tổng số lần ngắt mạch (circuit breaker) được kích hoạt cho một provider.",
    ["provider"]
)
PROVIDER_LATENCY_SECONDS = Histogram(
    "gateway_provider_latency_seconds",
    "Phân phối độ trễ của các request đến LLM provider.",
    ["provider", "model"]
)

# --- Token Metrics ---
INPUT_TOKENS_TOTAL = Counter(
    "gateway_input_tokens_total",
    "Tổng số token đầu vào được xử lý.",
    ["provider", "model"]
)
OUTPUT_TOKENS_TOTAL = Counter(
    "gateway_output_tokens_total",
    "Tổng số token đầu ra được tạo ra.",
    ["provider", "model"]
)

class Metrics:
    """Lớp trung gian cung cấp các hàm để ghi nhận số liệu một cách an toàn."""

    def increment_requests(self, method: str, endpoint: str):
        REQUESTS_TOTAL.labels(method=method, endpoint=endpoint).inc()

    def increment_requests_in_flight(self):
        REQUESTS_IN_FLIGHT.inc()

    def decrement_requests_in_flight(self):
        REQUESTS_IN_FLIGHT.dec()

    def increment_success(self):
        REQUESTS_SUCCESS_TOTAL.inc()

    def increment_failed(self, code: int = 500):
        REQUESTS_FAILED_TOTAL.labels(error_code=str(code)).inc()

    def increment_cache_hits(self):
        CACHE_HITS_TOTAL.inc()

    def increment_cache_write(self, amount: int = 1):
        CACHE_WRITE_TOTAL.inc(amount)

    def record_semantic_cache_latency(self, operation: str, seconds: float):
        SEMANTIC_CACHE_LATENCY_SECONDS.labels(operation=operation).observe(seconds)

    def increment_prompt_block(self):
        BLOCKED_REQUESTS_TOTAL.labels(reason="prompt_injection").inc()

    def increment_rate_limit(self):
        BLOCKED_REQUESTS_TOTAL.labels(reason="rate_limit").inc()

    def increment_provider_errors(self, provider: str, code: str):
        PROVIDER_ERRORS_TOTAL.labels(provider=provider, error_code=code).inc()

    def increment_circuit_breaker_opens(self, provider: str):
        CIRCUIT_BREAKER_OPENS_TOTAL.labels(provider=provider).inc()

    def record_latency(self, provider: str, model: str, seconds: float):
        PROVIDER_LATENCY_SECONDS.labels(provider=provider, model=model).observe(seconds)

    def increment_input_tokens(self, provider: str, model: str, count: int):
        INPUT_TOKENS_TOTAL.labels(provider=provider, model=model).inc(count)

    def increment_output_tokens(self, provider: str, model: str, count: int):
        OUTPUT_TOKENS_TOTAL.labels(provider=provider, model=model).inc(count)

# Khởi tạo một instance duy nhất để toàn bộ ứng dụng sử dụng
metrics = Metrics()