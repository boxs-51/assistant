from prometheus_client import Counter, Gauge, Histogram

class BaseMetrics:
    """Lớp Base cung cấp các metrics chung cho mọi Service trong hệ thống"""
    def __init__(self, namespace: str = "app"):
        # Định nghĩa các metric nền tảng (sử dụng namespace để phân biệt các app)
        self.REQUESTS_TOTAL = Counter(
            f"{namespace}_requests_total", "Tổng số request đi vào hệ thống.", ["method", "endpoint"]
        )
        self.REQUESTS_IN_FLIGHT = Gauge(
            f"{namespace}_requests_in_flight", "Số request đang được xử lý đồng thời."
        )
        self.REQUESTS_FAILED_TOTAL = Counter(
            f"{namespace}_requests_failed_total", "Tổng số request thất bại.", ["error_code"]
        )

    def increment_requests(self, method: str, endpoint: str):
        self.REQUESTS_TOTAL.labels(method=method, endpoint=endpoint).inc()

    def change_requests_in_flight(self, amount: int):
        self.REQUESTS_IN_FLIGHT.inc(amount) if amount > 0 else self.REQUESTS_IN_FLIGHT.dec(abs(amount))

    def increment_failed(self, code: int = 500):
        self.REQUESTS_FAILED_TOTAL.labels(error_code=str(code)).inc()

# Khởi tạo một global reference, ban đầu là None. Sẽ được gán khi init hệ thống.
metrics: BaseMetrics = None