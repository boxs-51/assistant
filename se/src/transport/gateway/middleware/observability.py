import time
import uuid
import structlog
from fastapi import Request
from opentelemetry import trace


logger = structlog.get_logger(__name__)

async def observability_middleware(request: Request, call_next):
    """
    Middleware trung tâm cho observability:
    1. Gắn request_id vào context của log.
    2. Ghi log cho request và response.
    3. Theo dõi số request đang xử lý (in-flight) và latency.
    4. Trả về correlation headers.
    """
    request_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start_time = time.time()
    logger.info("Request received", method=request.method, path=request.url.path)

    response = await call_next(request)

    process_time = time.time() - start_time
    
    current_span = trace.get_current_span()
    if current_span.is_recording():
        response.headers["x-trace-id"] = f"{current_span.get_span_context().trace_id:x}"
    response.headers["x-request-id"] = request_id

    logger.info("Request finished", status_code=response.status_code, process_time=round(process_time, 4))

    return response