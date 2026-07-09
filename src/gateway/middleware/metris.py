from prometheus_client import Counter, Histogram, Gauge
# Lưu ý: Thay đổi đường dẫn import này cho đúng với cấu trúc thư mục thực tế của bạn
from shared_core.observability.metrics import BaseMetrics
from shared_core.observability import init_observability, ObservabilityConfig

class GatewayMetrics(BaseMetrics):
    def __init__(self, namespace: str):
        # 1. Khởi tạo các metric chung từ BaseMetrics (REQUESTS_TOTAL, REQUESTS_IN_FLIGHT, REQUESTS_FAILED_TOTAL)
        super().__init__(namespace)
        
        # --- Bổ sung thêm Request Metric đặc thù của Gateway ---
        self.REQUESTS_SUCCESS_TOTAL = Counter(
            f"{namespace}_requests_success_total",
            "Tổng số request xử lý thành công."
        )

        # --- Cache Metrics ---
        self.CACHE_HITS_TOTAL = Counter(
            f"{namespace}_cache_hits_total",
            "Tổng số lần tìm thấy trong cache (cache hit)."
        )
        self.CACHE_MISSES_TOTAL = Counter(
            f"{namespace}_cache_misses_total",
            "Tổng số lần không tìm thấy trong cache (cache miss).",
            ["reason"]  # e.g., 'not_found', 'expired'
        )
        self.CACHE_WRITE_TOTAL = Counter(
            f"{namespace}_cache_write_total",
            "Tổng số lần ghi vào cache."
        )
        self.SEMANTIC_CACHE_LATENCY_SECONDS = Histogram(
            f"{namespace}_semantic_cache_latency_seconds",
            "Phân phối độ trễ của các hoạt động cache ngữ nghĩa.",
            ["operation"]  # 'get' hoặc 'set'
        )

        # --- Security Metrics ---
        self.BLOCKED_REQUESTS_TOTAL = Counter(
            f"{namespace}_blocked_requests_total",
            "Tổng số request bị chặn bởi các lớp bảo vệ.",
            ["reason"]  # e.g., 'rate_limit', 'prompt_injection'
        )

        # --- Provider Metrics ---
        self.PROVIDER_ERRORS_TOTAL = Counter(
            f"{namespace}_provider_errors_total",
            "Tổng số lỗi từ các LLM provider.",
            ["provider", "error_code"]
        )
        self.CIRCUIT_BREAKER_OPENS_TOTAL = Counter(
            f"{namespace}_circuit_breaker_opens_total",
            "Tổng số lần ngắt mạch (circuit breaker) được kích hoạt cho một provider.",
            ["provider"]
        )
        self.PROVIDER_LATENCY_SECONDS = Histogram(
            f"{namespace}_provider_latency_seconds",
            "Phân phối độ trễ của các request đến LLM provider.",
            ["provider", "model"]
        )

        # --- Token Metrics ---
        self.INPUT_TOKENS_TOTAL = Counter(
            f"{namespace}_input_tokens_total",
            "Tổng số token đầu vào được xử lý.",
            ["provider", "model"]
        )
        self.OUTPUT_TOKENS_TOTAL = Counter(
            f"{namespace}_output_tokens_total",
            "Tổng số token đầu ra được tạo ra.",
            ["provider", "model"]
        )

    # =================================================================
    # RECORDING METHODS
    # Sử dụng `self.` để trỏ đúng vào các metric đã đăng ký ở trên
    # =================================================================

    def increment_requests_in_flight(self):
        self.REQUESTS_IN_FLIGHT.inc()

    def decrement_requests_in_flight(self):
        self.REQUESTS_IN_FLIGHT.dec()

    def increment_success(self):
        self.REQUESTS_SUCCESS_TOTAL.inc()

    def increment_cache_hits(self):
        self.CACHE_HITS_TOTAL.inc()

    def increment_cache_write(self, amount: int = 1):
        self.CACHE_WRITE_TOTAL.inc(amount)

    def record_semantic_cache_latency(self, operation: str, seconds: float):
        self.SEMANTIC_CACHE_LATENCY_SECONDS.labels(operation=operation).observe(seconds)

    def increment_prompt_block(self):
        self.BLOCKED_REQUESTS_TOTAL.labels(reason="prompt_injection").inc()

    def increment_rate_limit(self):
        self.BLOCKED_REQUESTS_TOTAL.labels(reason="rate_limit").inc()

    def increment_provider_errors(self, provider: str, code: str):
        self.PROVIDER_ERRORS_TOTAL.labels(provider=provider, error_code=code).inc()

    def increment_circuit_breaker_opens(self, provider: str):
        self.CIRCUIT_BREAKER_OPENS_TOTAL.labels(provider=provider).inc()

    def record_latency(self, provider: str, model: str, seconds: float):
        self.PROVIDER_LATENCY_SECONDS.labels(provider=provider, model=model).observe(seconds)

    def increment_input_tokens(self, provider: str, model: str, count: int):
        self.INPUT_TOKENS_TOTAL.labels(provider=provider, model=model).inc(count)

    def increment_output_tokens(self, provider: str, model: str, count: int):
        self.OUTPUT_TOKENS_TOTAL.labels(provider=provider, model=model).inc(count)


# =================================================================
# SINGLETON INSTANCE INITIALIZATION
# =================================================================

# Khởi tạo một placeholder instance (Ban đầu là None)
metrics: GatewayMetrics = None

def setup_gateway_observability(config: ObservabilityConfig) -> GatewayMetrics:
    """
    Hàm cấu hình tập trung cho AI Gateway: Khởi tạo Logs, Traces 
    và đăng ký GatewayMetrics instance vào biến global.
    """
    global metrics
    # Gọi qua Core để thiết lập đồng bộ và trả về instance cụ thể của Gateway
    metrics = init_observability(config, custom_metrics_class=GatewayMetrics)
    return metrics