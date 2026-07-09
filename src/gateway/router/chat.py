import time
import hashlib
import structlog
import asyncio
from typing import List, Dict, Any, AsyncGenerator, Tuple

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from opentelemetry import trace

from ..schemas import GatewayChatRequest, GatewayResponse
from ..schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ..routing.exceptions import NoAvailableProviderError
from ..middleware.observability import gateway_metrics

router = APIRouter(tags=["LLM APIs"])
tracer = trace.get_tracer(__name__)
logger = structlog.get_logger(__name__)

async def parse_and_validate_request(request: Request) -> GatewayChatRequest:
    """1. Đọc JSON thô và ép sang schema Pydantic."""
    try:
        raw_body = await request.json()
    except Exception:
        logger.error("Invalid JSON format in request body")
        gateway_metrics.metrics.increment_failed(400)
        raise HTTPException(status_code=400, detail="Malformed JSON in request body.")

    try:
        return GatewayChatRequest(**raw_body)
    except ValidationError as val_err:
        logger.warning("Request schema validation failed", errors=val_err.errors())
        gateway_metrics.metrics.increment_failed(422)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            detail={
                "message": "Cấu trúc request hoặc messages không hợp lệ với hệ thống Multimodal Gateway.",
                "errors": val_err.errors()
            }
        )

def extract_prompt_and_cache_key(chat_request: GatewayChatRequest) -> Tuple[str, str]:
    """2. Bóc tách văn bản (user_prompt) và tính toán Cache Key từ nội dung Multimodal."""
    text_parts: List[str] = []
    multimedia_signatures: List[str] = []

    for msg in chat_request.messages:
        if msg.role != "user":
            continue
            
        content = msg.content
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if part.type.value == "text" and hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
                elif part.type.value in ["image", "audio", "video", "file"]:
                    media_obj = getattr(part, part.type.value, None)
                    if media_obj and getattr(media_obj, "base64_data", None):
                        base64_str = media_obj.base64_data
                        media_hash = hashlib.md5(base64_str.encode()).hexdigest()
                        multimedia_signatures.append(f"{part.type.value}:{media_hash}")

    user_prompt = " ".join(text_parts)
    cache_key = user_prompt
    if multimedia_signatures:
        cache_key += " | media_sign:" + ",".join(multimedia_signatures)
        
    return user_prompt, cache_key

def run_input_guardrail(request: Request, user_prompt: str) -> None:
    """3. Kiểm tra an toàn bảo mật cho Prompt (Input Guardrail)."""
    with tracer.start_as_current_span("input_guardrail") as span:
        if not request.app.state.input_fillter.validate(user_prompt):
            span.set_attribute("blocked", True)
            span.set_attribute("reason", "prompt_injection")
            logger.warning("Request blocked by Input Guardrail", reason="prompt_injection", prompt=user_prompt)
            gateway_metrics.metrics.increment_prompt_block()
            gateway_metrics.metrics.increment_failed(400)
            raise HTTPException(status_code=400, detail="Request blocked due to potential prompt injection.")

async def run_rate_limiter(request: Request, identity: Identity) -> None:
    """4. Kiểm tra giới hạn tần suất gọi API (Rate Limiting)."""
    with tracer.start_as_current_span("rate_limiter"):
        is_allowed, wait_time = await request.app.state.limiter.is_allowed(identity, cost=1)
        if not is_allowed:
            logger.warning("Request blocked by Rate Limiter", client_id=identity.get_rate_limit_key())
            gateway_metrics.metrics.increment_rate_limit()
            gateway_metrics.metrics.increment_failed(429)
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {wait_time:.2f} seconds.")

async def generate_stream_response(request: Request, body_dict: Dict[str, Any], start_time: float) -> AsyncGenerator[str, None]:
    """Hàm generator xử lý luồng dữ liệu Streaming từ LLM Router."""
    detected_provider = "unknown"
    detected_model = "unknown"
    
    stream_chunks = request.app.state.router.stream_with_fallback(
        http_client=request.app.state.http_client,
        body=body_dict
    )
    
    async for chunk in stream_chunks:
        if chunk.provider: detected_provider = chunk.provider
        if chunk.model: detected_model = chunk.model
            
        if chunk.usage:
            with tracer.start_as_current_span("token_tracking_stream") as t_span:
                in_t, out_t = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                gateway_metrics.metrics.increment_input_tokens(detected_provider, detected_model, in_t)
                gateway_metrics.metrics.increment_output_tokens(detected_provider, detected_model, out_t)
                t_span.set_attributes({"input_tokens": in_t, "output_tokens": out_t})
        
        yield chunk.to_sse()
    
    yield "data: [DONE]\n\n"
    
    gateway_metrics.metrics.increment_success()
    latency = time.time() - start_time
    gateway_metrics.metrics.record_latency(detected_provider, detected_model, latency)

async def handle_non_stream_response(request: Request, body_dict: Dict[str, Any], start_time: float) -> GatewayResponse:
    """Hàm xử lý luồng dữ liệu Non-Streaming (Đồng bộ) từ LLM Router."""
    with tracer.start_as_current_span("llm_routing_fallback") as span:
        gateway_response = await request.app.state.router.execute_with_fallback(
            http_client=request.app.state.http_client,
            body=body_dict
        )
        final_provider_name = gateway_response.provider
        span.set_attribute("final_provider", final_provider_name)
    
    with tracer.start_as_current_span("response_processing"):
        final_content = gateway_response.choices[0].message.content or ""
        
        with tracer.start_as_current_span("output_fillter"):
            safe_content = request.app.state.output_fillter.sanitize(final_content)
        gateway_response.choices[0].message.content = safe_content
        
        gateway_metrics.metrics.increment_success()
        latency = time.time() - start_time
        gateway_metrics.metrics.record_latency(final_provider_name, gateway_response.model, latency)

        with tracer.start_as_current_span("token_tracking") as token_span:
            input_tokens, output_tokens = gateway_response.usage.prompt_tokens, gateway_response.usage.completion_tokens
            gateway_metrics.metrics.increment_input_tokens(final_provider_name, gateway_response.model, input_tokens)
            gateway_metrics.metrics.increment_output_tokens(final_provider_name, gateway_response.model, output_tokens)
            token_span.set_attributes({"input_tokens": input_tokens, "output_tokens": output_tokens})
        
        return gateway_response

@router.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request, 
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint proxy thông minh xử lý Request kết nối đa mô hình (Multimodal Gateway)."""
    start_time = time.time()

    # 1. Đọc và Kiểm tra cấu trúc dữ liệu đầu vào (Validation)
    chat_request = await parse_and_validate_request(request)

    # 2. Bóc tách Prompt chính và sinh chuỗi khóa Cache định danh (Cache Key)
    user_prompt, cache_key = extract_prompt_and_cache_key(chat_request)

    # TỐI ƯU HÓA: Chạy song song các tác vụ không phụ thuộc (Guardrail & Rate Limit)
    await asyncio.gather( # type: ignore
        run_input_guardrail(request, user_prompt),
        run_rate_limiter(request, identity)
    )

    # 5. Kiểm tra Semantic Cache xem câu hỏi đã từng được trả lời chưa
    cached_result = await request.app.state.cache.get(cache_key)
    if cached_result:
        cached_response, _ = cached_result
        with tracer.start_as_current_span("process_cached_response"):
            gateway_metrics.metrics.increment_success() # type: ignore
            latency = time.time() - start_time
            gateway_metrics.metrics.record_latency("cache", "N/A", latency)
            
            safe_cached_response = request.app.state.output_fillter.sanitize(cached_response)
            return {"choices": [{"message": {"role": "assistant", "content": safe_cached_response}}]}

    body_dict = chat_request.model_dump()

    try:
        if chat_request.config.stream:
            return StreamingResponse(
                generate_stream_response(request, body_dict, start_time),
                media_type="text/event-stream"
            )
        else:
            return await handle_non_stream_response(request, body_dict, start_time)
    except NoAvailableProviderError as e:
        trace.get_current_span().record_exception(e)
        logger.critical("All providers are unavailable", error=str(e))
        gateway_metrics.metrics.increment_failed(503)
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")