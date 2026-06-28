from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import redis.asyncio as redis
import time
import psutil
from prometheus_client import generate_latest
import structlog
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import uuid

from .config import settings
from .limiter import RateLimiterManager
# --- Enterprise Routing Imports ---
from .router import ModelRouter
from .routing.registry import ProviderRegistry
from .routing.discovery import ProviderDiscovery
from .routing.exceptions import NoAvailableProviderError
from .routing.policies.routing_policy import RoutingPolicy
from .routing.policies.circuit_breaker import CircuitBreakerManager
# --------------------------------
from .security import authenticate_client, InputGuardrailAdapter, OutputGuardrailAdapter
from ..guardrail.guar import GuardrailSystem
# --- Refactored Cache Imports ---
from .caching import SemanticCache
from .semantic_cache.chroma_backend import ChromaCacheBackend
from .semantic_cache.embedding import EmbeddingService
# --------------------------------
from .metrics import metrics
from .logging_config import setup_logging
from .tracing_config import setup_tracing

app = FastAPI(title="AI Gateway")

@app.on_event("startup")
async def startup_event():
    """Khởi tạo các kết nối cần thiết khi server khởi động."""
    # Cấu hình logging ngay khi khởi động
    setup_logging(log_level=settings.LOG_LEVEL)
    # Cấu hình tracing
    setup_tracing(service_name=settings.GATEWAY_NAME)
    # Tự động instrument FastAPI app
    FastAPIInstrumentor.instrument_app(app)
    global logger
    logger = structlog.get_logger("gateway.main")
    app.state.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    app.state.limiter = RateLimiterManager(app.state.redis)
    # --- Enterprise Routing Initialization Flow ---
    provider_registry = ProviderRegistry()
    provider_discovery = ProviderDiscovery(registry=provider_registry)
    provider_discovery.run() # Chạy quá trình khám phá
    
    # Policy giờ đây cần danh sách các provider có sẵn để tự khởi tạo các quy tắc
    available_providers = provider_registry.list_all_providers()
    routing_policy = RoutingPolicy(providers=available_providers)
    
    circuit_breaker_manager = CircuitBreakerManager() # Tạo manager
    app.state.router = ModelRouter(
        providers=available_providers,
        routing_policy=routing_policy,
        circuit_breaker_manager=circuit_breaker_manager # Inject vào router
    )
    # -------------------------------------
    # --- New Guardrail Initialization ---
    guardrail_system = GuardrailSystem()
    app.state.input_guardrail = InputGuardrailAdapter(guardrail_system)
    app.state.output_guardrail = OutputGuardrailAdapter(guardrail_system)
    # ----------------------------------
    # --- Refactored Cache Initialization ---
    embedding_service = EmbeddingService()
    cache_backend = ChromaCacheBackend()
    app.state.cache = SemanticCache(backend=cache_backend, embedding_service=embedding_service)
    # ---------------------------------------
    app.state.http_client = httpx.AsyncClient(timeout=settings.PROVIDER_TIMEOUT)
    logger.info("Gateway startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    """Đóng các kết nối khi server tắt."""
    await app.state.redis.close()
    await app.state.http_client.aclose()

@app.middleware("http")
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

    metrics.increment_requests_in_flight()
    response = await call_next(request)
    metrics.decrement_requests_in_flight()

    process_time = time.time() - start_time
    # TODO: Ghi nhận latency của toàn bộ request vào một histogram mới
    
    # Thêm correlation headers vào response
    current_span = trace.get_current_span()
    if current_span.is_recording():
        response.headers["x-trace-id"] = f"{current_span.get_span_context().trace_id:x}"
    response.headers["x-request-id"] = request_id

    logger.info("Request finished", status_code=response.status_code, process_time=round(process_time, 4))

    return response

@app.post("/v1/chat/completions")
async def chat_completions_proxy(request: Request, client_id: str = Depends(authenticate_client)):
    """
    Endpoint chính, hoạt động như một proxy thông minh cho các request chat completion.
    """
    tracer = trace.get_tracer(__name__)

    metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id)
    start_time = time.time()
    
    # 1. Đọc và kiểm tra nội dung request
    try:
        body = await request.json()
        user_prompt = " ".join([msg['content'] for msg in body.get("messages", []) if msg['role'] == 'user'])
    except Exception:
        logger.error("Invalid request body", exc_info=True)
        metrics.increment_failed(400)
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # 2. Input Guardrail
    with tracer.start_as_current_span("input_guardrail") as span:
        if not app.state.input_guardrail.validate(user_prompt):
            span.set_attribute("blocked", True)
            span.set_attribute("reason", "prompt_injection")
            logger.warning("Request blocked by Input Guardrail", reason="prompt_injection", prompt=user_prompt)
            metrics.increment_prompt_block()
            metrics.increment_failed(400)
            raise HTTPException(status_code=400, detail="Request blocked due to potential prompt injection.")

    # 3. Rate Limiting
    with tracer.start_as_current_span("rate_limiter"):
        is_allowed, wait_time = await app.state.limiter.is_allowed(client_id, cost=1) # Cost = 1 request
        if not is_allowed:
            logger.warning("Request blocked by Rate Limiter", client_id=client_id)
            metrics.increment_rate_limit()
            metrics.increment_failed(429)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {wait_time:.2f} seconds.")

    # 4. Semantic Cache
    cached_result = await app.state.cache.get(user_prompt)
    if cached_result:
        cached_response, cached_embedding = cached_result
        with tracer.start_as_current_span("process_cached_response"):
            metrics.increment_success()
            latency = time.time() - start_time
            metrics.record_latency("cache", "N/A", latency) # Model không áp dụng cho cache
            # Vẫn cần quét output của cache để đảm bảo an toàn
            safe_cached_response = app.state.output_guardrail.sanitize(cached_response)
            return {"choices": [{"message": {"role": "assistant", "content": safe_cached_response}}]}
    else:
        # Nếu cache miss, embedding đã được tạo và có thể được tái sử dụng
        # Tuy nhiên, để đơn giản hóa luồng, chúng ta sẽ tạo lại nó trong hàm set()
        # Một cải tiến trong tương lai là truyền embedding này đi.
        pass

    # 5. & 6. Routing, Fallback và Gọi LLM Provider (gộp làm một)
    try:
        with tracer.start_as_current_span("llm_routing_fallback") as span:
            provider_response, final_provider = await app.state.router.execute_with_fallback(
                http_client=app.state.http_client,
                model=body.get("model"),
                body=body
            )
            span.set_attribute("final_provider", final_provider.name)
        
        # 7. Xử lý response và Output Guardrail
        is_streaming = body.get("stream", False)
        
        if is_streaming:
            # Trả về một generator đã được làm sạch
            return StreamingResponse(
                app.state.output_guardrail.sanitize_stream(provider_response.aiter_text()),
                media_type="text/event-stream"
            )
        else:
            with tracer.start_as_current_span("response_processing"):
                response_json = await provider_response.json()
                final_content = response_json["choices"][0]["message"]["content"]
                
                # Làm sạch đầu ra
                with tracer.start_as_current_span("output_guardrail"):
                    safe_content = app.state.output_guardrail.sanitize(final_content)
                response_json["choices"][0]["message"]["content"] = safe_content
                
                # 8. Cập nhật cache
                # Lấy embedding đã được tạo trong `cache.get` để tái sử dụng
                # TODO: Refactor để truyền embedding từ bước get
                # await app.state.cache.set(user_prompt, final_content, embedding=cached_embedding)
                
                metrics.increment_success()
                latency = time.time() - start_time
                metrics.record_latency(final_provider.name, body.get("model", "unknown"), latency)

                # 9. [MỚI] Theo dõi Token và Chi phí
                with tracer.start_as_current_span("token_tracking") as token_span:
                    # Giả lập lấy token usage từ response (thực tế cần parse từ response của provider)
                    usage = response_json.get("usage", {"prompt_tokens": 50, "completion_tokens": 150})
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)

                    # Ghi nhận vào Metrics
                    # metrics.record_input_tokens(final_provider.name, body.get("model"), input_tokens)
                    # metrics.record_output_tokens(final_provider.name, body.get("model"), output_tokens)
                    
                    # Ghi nhận vào Span Attributes
                    token_span.set_attribute("input_tokens", input_tokens)
                    token_span.set_attribute("output_tokens", output_tokens)
                
                return response_json
            
    except NoAvailableProviderError as e:
        # Lỗi này xảy ra khi tất cả các provider đều không khả dụng
        trace.get_current_span().record_exception(e)
        logger.critical("All providers are unavailable", error=str(e))
        metrics.increment_failed(503)
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")

# =================================================================
# HEALTH & STATUS ENDPOINTS
# =================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Endpoint đơn giản cho Kubernetes liveness probe."""
    return {"status": "ok"}

@app.get("/ready", tags=["Health"])
async def readiness_check():
    """
    Kiểm tra sự sẵn sàng của các dịch vụ phụ thuộc (Redis, LLM Providers).
    Sử dụng cho Kubernetes readiness probe.
    """
    try:
        # 1. Kiểm tra Redis
        await app.state.redis.ping()
        
        # 2. Kiểm tra các provider đã cấu hình
        # Gửi một request nhỏ, không tốn kém để kiểm tra kết nối
        for provider_name, provider in app.state.router.providers.items():
            # Ví dụ: OpenAI có endpoint /v1/models để kiểm tra
            if provider_name == "openai":
                 await app.state.http_client.get(f"{provider.api_url}/models", headers=provider.headers)

    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {str(e)}")

    return {"status": "ready"}

@app.get("/metrics", tags=["Health"])
def get_metrics():
    """Expose các số liệu cho Prometheus scrape."""
    return StreamingResponse(generate_latest(), media_type="text/plain")

@app.get("/stats", tags=["Health"])
async def get_stats():
    """Cung cấp thống kê hoạt động ở dạng JSON cho dashboard nội bộ."""
    process = psutil.Process()
    return {
        "gateway_name": settings.GATEWAY_NAME,
        "gateway_version": settings.GATEWAY_VERSION,
        "cpu_usage_percent": process.cpu_percent(interval=0.1),
        "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
        # Thêm các số liệu khác từ module metrics nếu cần
    }

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.gateway.base_gateway:app", 
        host="127.0.0.1", 
        port=8000, 
        reload=True
    )