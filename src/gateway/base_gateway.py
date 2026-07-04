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
from .config_loader.core import ConfigLoader, ConfigurationRegistry
from .limiter import RateLimiterManager
# --- Enterprise Routing Imports ---
from .router import ModelRouter
from .routing.registry import ProviderRegistry
from .routing.discovery import ProviderDiscovery
from .routing.exceptions import NoAvailableProviderError
from .routing.policies.routing_policy import RoutingPolicy
from ..circuit_breaker.circuit_breaker import CircuitBreakerManager
# --------------------------------
from .security import authenticate_client, InputGuardrailAdapter, OutputGuardrailAdapter
from ..guardrail.guar import GuardrailSystem
# --- Refactored Cache Imports ---
from .caching import SemanticCache
from .semantic_cache.chroma_backend import ChromaCacheBackend
from .semantic_cache.embedding import EmbeddingService
# --------------------------------

from shared_core.observability import ObservabilityConfig ,LoggingConfig, TracingConfig
from .import observability as gateway_metrics
app = FastAPI(title="AI Gateway")

@app.on_event("startup")
async def startup_event():
    """Khởi tạo các kết nối cần thiết khi server khởi động."""
    # 1. Tải cấu hình
    # Đây là bước đầu tiên và quan trọng nhất
    # Di chuyển logger ra ngoài để có thể truy cập toàn cục sau khi cấu hình
    global logger; logger = structlog.get_logger("gateway.main")
    
    loader = ConfigLoader(default_config_path="config/gateway/default.yaml")
    app_config = loader.load_config()
    ConfigurationRegistry.set_config(app_config)
    settings = ConfigurationRegistry.get_config()
    # Cấu hình logging ngay khi khởi động
    config = ObservabilityConfig(service_name=settings.gateway.name,
                                 service_version=settings.gateway.version,
                                 logging=LoggingConfig(level=settings.logging.level),
                                 tracing=TracingConfig(enable=settings.tracing.enable, otlp_endpoint=settings.tracing.otlp_endpoint)
                                )
    gateway_metrics.setup_gateway_observability(config)
    # Tự động instrument FastAPI app
    FastAPIInstrumentor.instrument_app(app)
    app.state.redis = redis.from_url(settings.redis.url, decode_responses=True)
    # --- Enterprise Routing Initialization Flow ---
    provider_registry = ProviderRegistry()
    provider_discovery = ProviderDiscovery(registry=provider_registry)
    provider_discovery.run() # Chạy quá trình khám phá
    
    # Policy giờ đây cần danh sách các provider có sẵn để tự khởi tạo các quy tắc
    available_providers = provider_registry.list_all_providers()
    routing_policy = RoutingPolicy(providers=available_providers)
    
    # --- Centralized Managers ---
    # CircuitBreakerManager giờ được dùng chung cho cả Router và Rate Limiter
    circuit_breaker_manager = CircuitBreakerManager()
    app.state.limiter = RateLimiterManager(app.state.redis, circuit_breaker_manager)
    logger.info(
        "Providers",
        providers=list(available_providers.keys())
    )
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
    app.state.http_client = httpx.AsyncClient(timeout=settings.provider.timeout)
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

    gateway_metrics.metrics.increment_requests_in_flight()
    response = await call_next(request)
    gateway_metrics.metrics.decrement_requests_in_flight()

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

    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id)
    start_time = time.time()
    
    # 1. Đọc và kiểm tra nội dung request
    try:
        body = await request.json()
        print(body)
        user_prompt = " ".join([msg['content'] for msg in body.get("messages", []) if msg['role'] == 'user'])
    except Exception:
        logger.error("Invalid request body", exc_info=True)
        gateway_metrics.metrics.increment_failed(400)
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # 2. Input Guardrail
    with tracer.start_as_current_span("input_guardrail") as span:
        if not app.state.input_guardrail.validate(user_prompt):
            span.set_attribute("blocked", True)
            span.set_attribute("reason", "prompt_injection")
            logger.warning("Request blocked by Input Guardrail", reason="prompt_injection", prompt=user_prompt)
            gateway_metrics.metrics.increment_prompt_block()
            gateway_metrics.metrics.increment_failed(400)
            raise HTTPException(status_code=400, detail="Request blocked due to potential prompt injection.")

    # 3. Rate Limiting
    with tracer.start_as_current_span("rate_limiter"):
        is_allowed, wait_time = await app.state.limiter.is_allowed(client_id, cost=1) # Cost = 1 request
        if not is_allowed:
            logger.warning("Request blocked by Rate Limiter", client_id=client_id)
            gateway_metrics.metrics.increment_rate_limit()
            gateway_metrics.metrics.increment_failed(429)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {wait_time:.2f} seconds.")

    # 4. Semantic Cache
    cached_result = await app.state.cache.get(user_prompt)
    if cached_result:
        cached_response, cached_embedding = cached_result
        with tracer.start_as_current_span("process_cached_response"):
            gateway_metrics.metrics.increment_success()
            latency = time.time() - start_time
            gateway_metrics.metrics.record_latency("cache", "N/A", latency) # Model không áp dụng cho cache
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
            # Router giờ trả về một GatewayResponse đã được chuẩn hóa
            gateway_response = await app.state.router.execute_with_fallback(
                http_client=app.state.http_client,
                body=body
            )
            # Lấy tên provider từ response gốc nếu cần
            # Lấy tên provider từ request gốc được lưu trong raw_response
            final_provider_name = "unknown"
            if gateway_response.raw_response and gateway_response.raw_response.request:
                 final_provider_name = gateway_response.raw_response.request.url.host
            span.set_attribute("final_provider", final_provider_name)
        
        # 7. Xử lý response và Output Guardrail
        is_streaming = body.get("stream", False)
        
        if is_streaming:
            async def stream_generator():
                """Generator để xử lý và yield các chunk đã được chuẩn hóa."""
                stream_chunks = await app.state.router.stream_with_fallback(
                    http_client=app.state.http_client,
                    body=body
                )
                async for chunk in stream_chunks:
                    # TODO: Output Guardrail cho từng chunk (nếu cần)
                    yield chunk.to_sse()
                
                # [FIX] Gửi thông điệp [DONE] theo chuẩn OpenAI khi stream kết thúc thành công
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream"
            )
        else:
            with tracer.start_as_current_span("response_processing"):
                # GatewayResponse đã có cấu trúc chuẩn, không cần bóc tách phức tạp
                final_content = gateway_response.choices[0].message.content or ""
                
                # Làm sạch đầu ra
                with tracer.start_as_current_span("output_guardrail"):
                    safe_content = app.state.output_guardrail.sanitize(final_content)
                gateway_response.choices[0].message.content = safe_content

                # 8. Cập nhật cache
                # await app.state.cache.set(user_prompt, final_content, ...)
                
                gateway_metrics.metrics.increment_success()
                latency = time.time() - start_time
                gateway_metrics.metrics.record_latency(final_provider_name, gateway_response.model, latency)

                # 9. [MỚI] Theo dõi Token và Chi phí
                with tracer.start_as_current_span("token_tracking") as token_span:
                    input_tokens = gateway_response.usage.prompt_tokens
                    output_tokens = gateway_response.usage.completion_tokens

                    # Ghi nhận vào Metrics
                    gateway_metrics.metrics.increment_input_tokens(final_provider_name, gateway_response.model, input_tokens)
                    gateway_metrics.metrics.increment_output_tokens(final_provider_name, gateway_response.model, output_tokens)
                    
                    # Ghi nhận vào Span Attributes
                    token_span.set_attribute("input_tokens", input_tokens)
                    token_span.set_attribute("output_tokens", output_tokens)
                
                # Trả về Pydantic model, FastAPI sẽ tự động chuyển thành JSON
                return gateway_response
            
    except NoAvailableProviderError as e:
        # Lỗi này xảy ra khi tất cả các provider đều không khả dụng
        trace.get_current_span().record_exception(e)
        logger.critical("All providers are unavailable", error=str(e))
        gateway_metrics.metrics.increment_failed(503)
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")

# =================================================================
# ADMIN ENDPOINTS
# =================================================================

@app.post("/admin/reload/routing", tags=["Admin"], dependencies=[Depends(authenticate_client)])
async def reload_routing_rules(request: Request):
    """
    Endpoint quản trị để tải lại nóng (hot-reload) các quy tắc định tuyến từ file YAML.
    Yêu cầu xác thực.
    """
    success = await app.state.router.routing_policy.reload_rules()
    if success:
        return {"status": "success", "message": "Routing rules reloaded successfully."}
    else:
        raise HTTPException(status_code=500, detail="Failed to reload routing rules. Check logs for details.")

@app.get("/admin/circuit-breakers/status", tags=["Admin"], dependencies=[Depends(authenticate_client)])
async def get_circuit_breaker_statuses(request: Request):
    """
    Endpoint quản trị để xem trạng thái hiện tại của tất cả các Circuit Breaker.
    Cung cấp thông tin chi tiết về trạng thái (open, closed, half-open),
    số lỗi, và thời gian xảy ra lỗi cuối cùng.
    Yêu cầu xác thực.
    """
    # CircuitBreakerManager được inject vào ModelRouter, ta có thể lấy nó từ đó.
    circuit_breaker_manager = request.app.state.router.circuit_breaker_manager
    statuses = await circuit_breaker_manager.get_all_statuses()
    return JSONResponse(content=statuses)


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
    settings = ConfigurationRegistry.get_config()
    process = psutil.Process()
    return {
        "gateway_name": settings.gateway.name,
        "gateway_version": settings.gateway.version,
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