import logging
import sys
import structlog
from opentelemetry import trace
from .config import LoggingConfig

def inject_otel_context(logger, method_name, event_dict):
    span = trace.get_current_span()
    if span.is_recording():
        context = span.get_span_context()
        event_dict["trace_id"] = f"{context.trace_id:x}"
        event_dict["span_id"] = f"{context.span_id:x}"
    return event_dict

def init_logging(config: LoggingConfig):
    # 1. Đặt bộ xử lý (processors) dùng chung cho cả 2 môi trường
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        inject_otel_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # 2. Lựa chọn Renderer cuối cùng dựa trên môi trường
    if config.development:
        # Renderer tối ưu cho việc đọc bằng mắt trên Terminal
        formatter_processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # Renderer tối ưu cho việc parse log trên Production (K8s, Loki, ELK)
        formatter_processor = structlog.processors.JSONRenderer()

    # 3. Cấu hình Standard Logging của Python để đẩy hết về Structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=config.level.upper(),
    )

    structlog.configure(
        processors=shared_processors + [formatter_processor],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 4. HẠ NHIỆT CÁC THƯ VIỆN SPAM LOG
    # Ép các thư viện ồn ào chỉ được log khi có lỗi (WARNING trở lên)
    quiet_loggers = [
        "httpx", 
        "huggingface_hub", 
        "sentence_transformers", 
        "chromadb", 
        "urllib3"
    ]
    for logger_name in quiet_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)