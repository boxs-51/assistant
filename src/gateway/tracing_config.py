#tracing_config.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from .config import settings

def setup_tracing(service_name: str = "ai-gateway"):
    """
    Cấu hình OpenTelemetry để thu thập và xuất trace.

    Hàm này thiết lập một TracerProvider, định nghĩa các thuộc tính của dịch vụ,
    và cấu hình một exporter để gửi dữ liệu trace đến một collector (ví dụ: Jaeger, Tempo).
    """
    if not settings.ENABLE_TRACING:
        return

    # 1. Tạo một resource để định danh dịch vụ của bạn
    resource = Resource(attributes={
        "service.name": service_name,
        "service.version": settings.GATEWAY_VERSION,
    })

    # 2. Thiết lập TracerProvider với resource đã tạo
    provider = TracerProvider(resource=resource)

    # 3. Cấu hình OTLP Exporter để gửi trace qua HTTP
    # Endpoint được lấy từ file config
    exporter = OTLPSpanExporter(endpoint=f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces")

    # 4. Sử dụng BatchSpanProcessor để gửi trace theo lô, giúp tối ưu hiệu suất
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # 5. Đặt provider vừa cấu hình làm provider mặc định cho toàn bộ ứng dụng
    trace.set_tracer_provider(provider)

    # 6. [MỚI] Tự động instrument các cuộc gọi HTTP của thư viện httpx
    HTTPXClientInstrumentor().instrument()