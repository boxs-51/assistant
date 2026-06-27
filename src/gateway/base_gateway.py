from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
import httpx
import redis.asyncio as redis
import time

from .config import settings
from .limiter import TokenBucketRateLimiter
from .routing import ModelRouter, NoAvailableProviderError
from .security import authenticate_client, InputGuardrail, OutputGuardrail
from .caching import SemanticCache
from .observability import metrics

app = FastAPI(title="AI Gateway")

@app.on_event("startup")
async def startup_event():
    """Khởi tạo các kết nối cần thiết khi server khởi động."""
    app.state.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    app.state.limiter = TokenBucketRateLimiter(app.state.redis)
    app.state.router = ModelRouter()
    app.state.input_guardrail = InputGuardrail()
    app.state.output_guardrail = OutputGuardrail()
    app.state.cache = SemanticCache()
    app.state.http_client = httpx.AsyncClient(timeout=settings.PROVIDER_TIMEOUT)

@app.on_event("shutdown")
async def shutdown_event():
    """Đóng các kết nối khi server tắt."""
    await app.state.redis.close()
    await app.state.http_client.aclose()

@app.post("/v1/chat/completions")
async def chat_completions_proxy(request: Request, client_id: str = Depends(authenticate_client)):
    """
    Endpoint chính, hoạt động như một proxy thông minh cho các request chat completion.
    """
    start_time = time.time()
    
    # 1. Đọc và kiểm tra nội dung request
    try:
        body = await request.json()
        user_prompt = " ".join([msg['content'] for msg in body.get("messages", []) if msg['role'] == 'user'])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # 2. Input Guardrail
    if not app.state.input_guardrail.validate(user_prompt):
        metrics.increment_blocked_requests("prompt_injection")
        raise HTTPException(status_code=400, detail="Request blocked due to potential prompt injection.")

    # 3. Rate Limiting
    is_allowed, wait_time = await app.state.limiter.is_allowed(client_id, cost=1) # Cost = 1 request
    if not is_allowed:
        metrics.increment_blocked_requests("rate_limit")
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {wait_time:.2f} seconds.")

    # 4. Semantic Cache
    cached_response = await app.state.cache.get(user_prompt)
    if cached_response:
        metrics.increment_cache_hits()
        latency = time.time() - start_time
        metrics.record_latency(latency, "cache")
        # Vẫn cần quét output của cache để đảm bảo an toàn
        safe_cached_response = app.state.output_guardrail.sanitize(cached_response)
        return {"choices": [{"message": {"role": "assistant", "content": safe_cached_response}}]}
    
    # 5. & 6. Routing, Fallback và Gọi LLM Provider (gộp làm một)
    try:
        provider_response, final_provider = await app.state.router.execute_with_fallback(
            http_client=app.state.http_client,
            model=body.get("model"),
            body=body
        )
        
        # 7. Xử lý response và Output Guardrail
        is_streaming = body.get("stream", False)
        
        if is_streaming:
            # Trả về một generator đã được làm sạch
            return StreamingResponse(
                app.state.output_guardrail.sanitize_stream(provider_response.aiter_text()),
                media_type="text/event-stream"
            )
        else:
            response_json = await provider_response.json()
            final_content = response_json["choices"][0]["message"]["content"]
            
            # Làm sạch đầu ra
            safe_content = app.state.output_guardrail.sanitize(final_content)
            response_json["choices"][0]["message"]["content"] = safe_content
            
            # 8. Cập nhật cache
            await app.state.cache.set(user_prompt, final_content) # Lưu nội dung gốc vào cache
            
            latency = time.time() - start_time
            metrics.record_latency(latency, final_provider.name)
            # TODO: Đếm và ghi nhận token
            
            return response_json
            
    except NoAvailableProviderError as e:
        # Lỗi này xảy ra khi tất cả các provider đều không khả dụng
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")
