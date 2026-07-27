from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from .config import ObservabilityConfig

def init_tracing(config: ObservabilityConfig):
    if not config.tracing.enable:
        return

    resource = Resource(attributes={
        "service.name": config.service_name,
        "service.version": config.service_version,
    })

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{config.tracing.otlp_endpoint}/v1/traces")
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    
    trace.set_tracer_provider(provider)
    
    # Tự động tracking HTTPX nếu service có sử dụng
    HTTPXClientInstrumentor().instrument()