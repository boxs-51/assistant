#logging_config.py
import logging
import sys
import structlog
from opentelemetry import trace

def inject_otel_context(logger, method_name, event_dict):
    """
    Processor tùy chỉnh để inject OpenTelemetry trace_id và span_id vào log record.
    """
    span = trace.get_current_span()
    if span.is_recording():
        context = span.get_span_context()
        event_dict["trace_id"] = f"{context.trace_id:x}"
        event_dict["span_id"] = f"{context.span_id:x}"
    return event_dict


def setup_logging(log_level: str = "INFO"):
    """
    Cấu hình structured logging (JSON) cho toàn bộ ứng dụng.

    Hàm này thiết lập một chuỗi các "processors" của structlog để làm giàu
    và định dạng log records trước khi xuất ra dưới dạng JSON.
    """
    # Cấu hình chung cho thư viện logging của Python
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level.upper(),
    )

    # Cấu hình cho structlog
    structlog.configure(
        processors=[
            # Hợp nhất context từ structlog.contextvars vào log record.
            # Đây là cách để các thông tin như request_id được tự động thêm vào mọi log.
            structlog.contextvars.merge_contextvars,
            # Inject trace_id và span_id từ OpenTelemetry
            inject_otel_context,
            # Thêm các thuộc tính mặc định như log_level và timestamp.
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Xử lý thông tin exception.
            structlog.processors.format_exc_info,
            # Đảm bảo các chuỗi là unicode.
            structlog.processors.UnicodeDecoder(),
            # Render log record cuối cùng thành một chuỗi JSON.
            structlog.processors.JSONRenderer(),
        ],
        # Sử dụng logger chuẩn của Python.
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )