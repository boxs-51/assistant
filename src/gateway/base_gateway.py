from fastapi import FastAPI, Request, HTTPException, Depends, status , Query, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse, Response
from pydantic import ValidationError
import httpx
import redis.asyncio as redis
import time, anyio
import psutil
from prometheus_client import generate_latest
import structlog
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from typing import List, Dict, Any, Literal, Optional
import hashlib
import io

from .schemas import GatewayChatRequest
import uuid
from .config.core import ConfigLoader, ConfigurationRegistry
from .limiter import RateLimiterManager
# --- Enterprise Routing Imports ---
from .routing import ModelRouter # Sẽ được sửa đổi bên dưới
from .routing.exceptions import NoAvailableProviderError, ProviderError
from .circuit_breaker import CircuitBreakerManager
# --------------------------------
from .security import authenticate_client, InputGuardrailAdapter, OutputGuardrailAdapter
from ..guardrail.guar import GuardrailSystem
# --- Refactored Cache Imports ---
from .caching import SemanticCache
from .caching.chroma_backend import ChromaCacheBackend
from .caching.embedding import EmbeddingService
# --------------------------------

from shared_core.observability import ObservabilityConfig ,LoggingConfig, TracingConfig
from .import observability as gateway_metrics
from .config import settings

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
    #settings = ConfigurationRegistry.get_config()
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
    # --- Centralized Managers ---
    # CircuitBreakerManager giờ được dùng chung cho cả Router và Rate Limiter
    circuit_breaker_manager = CircuitBreakerManager()
    app.state.limiter = RateLimiterManager(app.state.redis, circuit_breaker_manager)

    app.state.router = ModelRouter(
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
    Endpoint proxy thông minh, ép dữ liệu đầu vào sang GatewayChatRequest Schema
    và bắt lỗi Validation để phản hồi lập tức nếu client gửi sai cấu trúc.
    """
    tracer = trace.get_tracer(__name__)
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id)
    start_time = time.time()
    
    final_provider_name = "unknown" 

    # 1. Đọc JSON thô và ÉP SANG SCHEMAS Pydantic (GatewayChatRequest)
    try:
        raw_body = await request.json()
    except Exception:
        logger.error("Invalid JSON format in request body")
        gateway_metrics.metrics.increment_failed(400)
        raise HTTPException(status_code=400, detail="Malformed JSON in request body.")

    try:
        # Ép kiểu dữ liệu sang Pydantic Object (Tự động kích hoạt validation)
        chat_request = GatewayChatRequest(**raw_body)
    except ValidationError as val_err:
        # TRẢ LỜI LUÔN NẾU MESSAGES/REQUEST BỊ SAI ĐỊNH DẠNG SCHEMAS
        logger.warning("Request schema validation failed", errors=val_err.errors())
        gateway_metrics.metrics.increment_failed(422)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            detail={
                "message": "Cấu trúc request hoặc messages không hợp lệ với hệ thống Multimodal Gateway.",
                "errors": val_err.errors() # Trả về chi tiết các trường bị lỗi (Ví dụ: sai kiểu dữ liệu, thiếu role,...)
            }
        )

    # 2. Bóc tách văn bản (user_prompt) & Multimedia Signature dựa trên Pydantic Object mới
    text_parts: List[str] = []
    multimedia_signatures: List[str] = []

    for msg in chat_request.messages:
        if msg.role != "user":
            continue
            
        content = msg.content
        
        # Trường hợp nội dung là chuỗi phẳng (Text cũ, tương thích ngược)
        if isinstance(content, str):
            text_parts.append(content)
            
        # Trường hợp nội dung là mảng List[MessageContentPart] chuẩn Schema mới
        elif isinstance(content, list):
            for part in content:
                # Vì part lúc này là một thực thể đã qua validate, bạn truy cập trực tiếp bằng dấu chấm (.)
                if part.type.value == "text" and part.text:
                    text_parts.append(part.text)
                
                elif part.type.value in ["image", "audio", "video", "file"]:
                    # Lấy object media tương ứng dựa theo type (ví dụ: part.image hoặc part.file)
                    media_obj = getattr(part, part.type.value, None)
                    if media_obj and getattr(media_obj, "base64_data", None):
                        base64_str = media_obj.base64_data
                        media_hash = hashlib.md5(base64_str.encode()).hexdigest()
                        multimedia_signatures.append(f"{part.type.value}:{media_hash}")

    user_prompt = " ".join(text_parts)
    
    # Tạo Cache Key hợp nhất từ Text + Chữ ký đa phương tiện
    cache_key = user_prompt
    if multimedia_signatures:
        cache_key += " | media_sign:" + ",".join(multimedia_signatures)

    # Chuyển đổi chat_request Pydantic object thành dictionary để truyền vào router xử lý
    # (Có thể giữ nguyên object nếu Router của bạn chấp nhận Pydantic Model)
    body_dict = chat_request.model_dump()

    # 3. Input Guardrail
    with tracer.start_as_current_span("input_guardrail") as span:
        if not app.state.input_guardrail.validate(user_prompt):
            span.set_attribute("blocked", True)
            span.set_attribute("reason", "prompt_injection")
            logger.warning("Request blocked by Input Guardrail", reason="prompt_injection", prompt=user_prompt)
            gateway_metrics.metrics.increment_prompt_block()
            gateway_metrics.metrics.increment_failed(400)
            raise HTTPException(status_code=400, detail="Request blocked due to potential prompt injection.")

    # 4. Rate Limiting
    with tracer.start_as_current_span("rate_limiter"):
        is_allowed, wait_time = await app.state.limiter.is_allowed(client_id, cost=1)
        if not is_allowed:
            logger.warning("Request blocked by Rate Limiter", client_id=client_id)
            gateway_metrics.metrics.increment_rate_limit()
            gateway_metrics.metrics.increment_failed(429)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {wait_time:.2f} seconds.")

    # 5. Semantic Cache
    cached_result = await app.state.cache.get(cache_key)
    if cached_result:
        cached_response, _ = cached_result
        with tracer.start_as_current_span("process_cached_response"):
            gateway_metrics.metrics.increment_success()
            latency = time.time() - start_time
            gateway_metrics.metrics.record_latency("cache", "N/A", latency)
            
            safe_cached_response = app.state.output_guardrail.sanitize(cached_response)
            return {"choices": [{"message": {"role": "assistant", "content": safe_cached_response}}]}

    # 6. Kiểm tra chế độ Streaming / Non-Streaming trực tiếp từ Object
    is_streaming = chat_request.stream

    try:
        if is_streaming:
            async def stream_generator():
                detected_provider = "unknown"
                detected_model = "gemini-model"
                
                stream_chunks = app.state.router.stream_with_fallback(
                    http_client=app.state.http_client,
                    body=body_dict
                )
                
                async for chunk in stream_chunks:
                    if chunk.provider:
                        detected_provider = chunk.provider
                    if chunk.model:
                        detected_model = chunk.model
                        
                    if chunk.usage:
                        with tracer.start_as_current_span("token_tracking_stream") as t_span:
                            in_t = chunk.usage.prompt_tokens
                            out_t = chunk.usage.completion_tokens
                            
                            gateway_metrics.metrics.increment_input_tokens(detected_provider, detected_model, in_t)
                            gateway_metrics.metrics.increment_output_tokens(detected_provider, detected_model, out_t)
                            t_span.set_attributes({"input_tokens": in_t, "output_tokens": out_t})
                    
                    yield chunk.to_sse()
                
                yield "data: [DONE]\n\n"
                
                gateway_metrics.metrics.increment_success()
                latency = time.time() - start_time
                gateway_metrics.metrics.record_latency(detected_provider, detected_model, latency)

            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream"
            )
            
        else:
            # 7. Xử lý luồng Non-Streaming dữ liệu Multimodal
            with tracer.start_as_current_span("llm_routing_fallback") as span:
                gateway_response = await app.state.router.execute_with_fallback(
                    http_client=app.state.http_client,
                    body=body_dict
                )
                final_provider_name = gateway_response.provider
                span.set_attribute("final_provider", final_provider_name)
            
            with tracer.start_as_current_span("response_processing"):
                final_content = gateway_response.choices[0].message.content or ""
                
                # Output Guardrail làm sạch văn bản đầu ra
                with tracer.start_as_current_span("output_guardrail"):
                    safe_content = app.state.output_guardrail.sanitize(final_content)
                gateway_response.choices[0].message.content = safe_content

                # Cập nhật cache dựa trên mã khóa cache_key hợp nhất
                # await app.state.cache.set(cache_key, safe_content)
                
                gateway_metrics.metrics.increment_success()
                latency = time.time() - start_time
                gateway_metrics.metrics.record_latency(final_provider_name, gateway_response.model, latency)

                # Theo dõi Token sử dụng
                with tracer.start_as_current_span("token_tracking") as token_span:
                    input_tokens = gateway_response.usage.prompt_tokens
                    output_tokens = gateway_response.usage.completion_tokens

                    gateway_metrics.metrics.increment_input_tokens(final_provider_name, gateway_response.model, input_tokens)
                    gateway_metrics.metrics.increment_output_tokens(final_provider_name, gateway_response.model, output_tokens)
                    
                    token_span.set_attribute("input_tokens", input_tokens)
                    token_span.set_attribute("output_tokens", output_tokens)
                
                return gateway_response
            
    except NoAvailableProviderError as e:
        trace.get_current_span().record_exception(e)
        logger.critical("All providers are unavailable", error=str(e))
        gateway_metrics.metrics.increment_failed(503)
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")
    
@app.post("/v1/embeddings", tags=["LLM APIs"])
async def embeddings_proxy(request: Request, client_id: str = Depends(authenticate_client)):
    """
    Endpoint để tạo vector embeddings cho văn bản.
    """
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # TODO: Thêm Rate Limiting và Caching nếu cần

    try:
        # Sử dụng phương thức mới của router
        response_data = await app.state.router.execute_embeddings(
            http_client=app.state.http_client,
            body=body
        )
        return JSONResponse(content=response_data)
    except NoAvailableProviderError as e:
        logger.critical("All providers are unavailable for embeddings", error=str(e))
        raise HTTPException(status_code=503, detail="Service Unavailable: All providers for embeddings are down.")

@app.get("/v1/models", tags=["LLM APIs"])
async def list_models_proxy(
    request: Request,
    provider_name: str,
    client_id: str = Depends(authenticate_client),
):
    """
    Endpoint để lấy danh sách các model có sẵn từ một provider.
    Mặc định lấy từ 'openai' nếu không có provider_name nào được chỉ định.
    """
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id)

    # Nếu chỉ list models, mặc định là 'openai' để tương thích ngược


    try:
        provider = app.state.router.providers.get(provider_name)

        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not configured or found.")

        # Lấy danh sách tất cả các model
        models_data = await provider.models(
            http_client=app.state.http_client, 
            timeout=settings.provider.timeout
        )
        enriched_list = provider.capability_manager.enrich_capabilities(models_data)
        return enriched_list

    except NotImplementedError:
        logger.warning("Requested model functionality not implemented for provider", provider=provider_name)
        raise HTTPException(status_code=501, detail=f"The requested model functionality is not implemented for provider '{provider_name}'.")
    except Exception as e:
        logger.error("Failed to process model request", provider=provider_name, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to process model request for provider '{provider_name}'.")

@app.get("/v1/models/{model_id:path}", tags=["LLM APIs"])
async def get_model_details_proxy(
    request: Request,
    model_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp (e.g., gemini, openai)"),
    client_id: str = Depends(authenticate_client)
):
    """
    Endpoint để lấy thông tin chi tiết của một model cụ thể từ một provider.
    """
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id, model_id=model_id, provider_name=provider_name)

    try:
        provider = app.state.router.providers.get(provider_name)

        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not configured or found.")

        # Gọi phương thức model của provider đã chọn
        model_data = await provider.model(
            http_client=app.state.http_client,
            timeout=settings.provider.timeout,
            model=model_id
        )
        enriched_list = provider.capability_manager.enrich_capabilities(model_data)
        return enriched_list

    except NotImplementedError:
        logger.warning("Get model details endpoint not implemented for provider", provider=provider_name)
        raise HTTPException(status_code=501, detail=f"Fetching model details is not implemented for provider '{provider_name}'.")
    except Exception as e:
        logger.error("Failed to fetch model details", provider=provider_name, model=model_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch model details from provider '{provider_name}'.")

@app.get("/v1/files")
async def list_files_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    page_size: Optional[int] = Query(None, alias="page_size"),
    page_token: Optional[str] = Query(None, alias="page_token"),
    client_id: str = Depends(authenticate_client)
):
    """Endpoint lấy danh sách các file có sẵn từ Provider."""
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id, provider_name=provider_name)

    provider = app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        files_list = await provider.list_files(
            http_client=app.state.http_client,
            timeout=settings.provider.timeout,
            page_size=page_size,
            page_token=page_token
        )
        return files_list
    except Exception as e:
        logger.error("Failed to list files", provider=provider_name, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve files list.")
    
@app.post("/v1/files")
async def upload_file_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    display_name: Optional[str] = Query(None, description="Tên hiển thị tùy chọn"),
    file: UploadFile = File(..., description="Tệp tin cần tải lên"),
    client_id: str = Depends(authenticate_client)
):
    """Endpoint tải tệp tin lên hệ thống lưu trữ của Provider."""
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id, provider_name=provider_name)

    provider = app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        # Đọc dữ liệu file tạm thời hoặc truyền thẳng object qua file_path tùy theo thiết kế FileHelper của bạn
        # Ở đây giả định truyền UploadFile object hoặc một file-like object vào adapter
        upload_result = await provider.upload_file(
            http_client=app.state.http_client,
            timeout=settings.provider.timeout,
            file_path=file,  # Hoặc lưu tạm ra ổ đĩa rồi truyền path chuỗi tùy FileHelper của bạn xử lý
            display_name=display_name or file.filename
        )
        return upload_result
    except Exception as e:
        logger.error("Failed to upload file", provider=provider_name, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to upload file.")
    
@app.get("/v1/files/{file_id:path}")
async def get_or_download_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    action: Literal["metadata", "download"] = Query("metadata", description="Hành động: lấy thông tin hoặc tải file"),
    client_id: str = Depends(authenticate_client)
):
    """Endpoint lấy thông tin chi tiết (metadata) HOẶC tải nội dung nhị phân của một file."""
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id, file_id=file_id, provider_name=provider_name)

    provider = app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        # BƯỚC 1: Lấy thông tin metadata của file trước
        file_metadata = await provider.get_file(
            http_client=app.state.http_client,
            timeout=settings.provider.timeout,
            file_name=file_id
        )

        # BƯỚC 2: Rẽ nhánh xử lý dựa vào tham số `action`
        if action == "metadata":
            return file_metadata
        
        elif action == "download":
            if not file_metadata.uri:
                raise HTTPException(status_code=400, detail="The requested file does not expose a valid download URI.")
            
            # Gọi hàm download_file đã viết thông qua uri có sẵn trong DTO
            file_bytes = await provider.download_file(
                http_client=app.state.http_client,
                timeout=settings.provider.timeout,
                uri=file_metadata.uri
            )
            
            # Trả về luồng nhị phân kèm đúng định dạng file gốc
            return StreamingResponse(
                io.BytesIO(file_bytes),
                media_type=file_metadata.mime_type or "application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={file_metadata.filename or 'file'}"}
            )

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.error("Error processing file request", provider=provider_name, file_id=file_id, action=action, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to process file request for '{file_id}'.")

@app.delete("/v1/files/{file_id:path}")
async def delete_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    client_id: str = Depends(authenticate_client)
):
    """Endpoint để xóa một tệp cụ thể ra khỏi hệ thống của provider."""
    gateway_metrics.metrics.increment_requests(request.method, request.url.path)
    structlog.contextvars.bind_contextvars(client_id=client_id, file_id=file_id, provider_name=provider_name)

    provider = app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        success = await provider.delete_file(
            http_client=app.state.http_client,
            timeout=settings.provider.timeout,
            file_name=file_id
        )
        if success:
            return Response(status_code=204)  # 204 No Content là chuẩn RESTful khi xóa thành công
        else:
            raise HTTPException(status_code=400, detail=f"Provider failed to delete file '{file_id}'.")
            
    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.error("Failed to delete file", provider=provider_name, file_id=file_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"An error occurred while deleting file '{file_id}'.")
    

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