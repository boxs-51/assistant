import time
import hashlib
import structlog
import asyncio
import json
from typing import List, Dict, Any, AsyncGenerator, Tuple, Optional

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from opentelemetry import trace

from ...schemas import (
    GatewayChatRequest, GatewayResponse,
    GatewayMessage, GatewayToolCall, 
    GatewayToolResult, FinishReason, 
    MessageContentType, TextContent, 
    MessageContentPart)
from ...schemas.event import BaseEvent
from ...schemas.identity import Identity
from ...schemas.session import Session
from ..authentication.dependency import get_current_identity
from ...provider.exceptions import NoAvailableProviderError
from ..middleware.observability import gateway_metrics
from ...tool import GatewayToolManager
from ...context.manager import ContextEngine

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

async def generate_stream_response(
    request: Request,
    chat_request: GatewayChatRequest,
    identity: Identity,
    start_time: float
) -> AsyncGenerator[str, None]:
    """Hàm generator xử lý luồng dữ liệu Streaming từ LLM Router."""
    tool_manager: GatewayToolManager = request.app.state.tool_manager
    max_tool_iterations = 5

    accessible_tools = await tool_manager.get_accessible_tools(identity)
    chat_request.tools = accessible_tools

    current_messages = chat_request.messages

    for i in range(max_tool_iterations):
        with tracer.start_as_current_span(f"stream_tool_loop_iteration_{i}") as loop_span:
            loop_span.set_attribute("iteration", i)

            body_dict = chat_request.model_dump(exclude_none=True)
            body_dict["messages"] = [msg.model_dump(exclude_none=True) for msg in current_messages]

            stream_chunks = request.app.state.router.stream_with_fallback(
                http_client=request.app.state.http_client,
                body=body_dict
            )

            # Biến để tích lũy thông tin từ các chunk
            finish_reason: Optional[FinishReason] = None
            tool_calls: List[GatewayToolCall] = []
            assistant_message_content = ""
            
            # 1. Xử lý stream từ LLM
            async for chunk in stream_chunks:
                yield chunk.to_sse() # Gửi chunk gốc về cho client ngay lập tức

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Tích lũy tool_calls và content từ các delta
                if choice.delta.content:
                    assistant_message_content += choice.delta.content
                if choice.delta.tool_calls:
                    # Logic tích lũy tool_calls từ các chunk
                    for tool_call_delta in choice.delta.tool_calls:
                        if len(tool_calls) <= tool_call_delta.index:
                            tool_calls.append(tool_call_delta)
                        else:
                            existing_call = tool_calls[tool_call_delta.index]
                            if tool_call_delta.id: existing_call.id = tool_call_delta.id
                            if tool_call_delta.function.name: existing_call.function.name += tool_call_delta.function.name
                            if tool_call_delta.function.arguments: existing_call.function.arguments += tool_call_delta.function.arguments

            # 2. Kiểm tra nếu không có tool call, kết thúc
            if finish_reason != FinishReason.TOOL_CALLS or not tool_calls:
                gateway_metrics.metrics.increment_success()
                latency = time.time() - start_time
                # Giả sử provider và model đã được ghi nhận trong stream
                # gateway_metrics.metrics.record_latency(detected_provider, detected_model, latency)
                yield "data: [DONE]\n\n"
                return

            # 3. Nếu có, thực thi tool
            logger.info("LLM requested tool calls via stream", tool_calls=[tc.function.name for tc in tool_calls])

            # Thêm message của assistant (đã tích lũy) vào lịch sử
            current_messages.append(GatewayMessage(role="assistant", content=assistant_message_content, tool_calls=tool_calls))

            tool_results: List[GatewayToolResult] = []
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                
                result_content = await tool_manager.execute_tool(tool_name, tool_args, identity)
                
                tool_results.append(GatewayToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_name,
                    content=result_content
                ))
            
            # 4. Thêm message kết quả tool vào lịch sử và lặp lại
            current_messages.append(GatewayMessage(role="tool", tool_results=tool_results, content=""))

    logger.error("Streaming tool execution loop exceeded maximum iterations.")
    yield f"data: {json.dumps({'error': 'Tool execution loop exceeded maximum iterations.'})}\n\n"
    yield "data: [DONE]\n\n"

async def handle_non_stream_response(
    request: Request,
    chat_request: GatewayChatRequest,
    identity: Identity,
    start_time: float
) -> GatewayResponse:
    
    """Hàm xử lý luồng dữ liệu Non-Streaming (Đồng bộ) từ LLM Router."""
    tool_manager: GatewayToolManager = request.app.state.tool_manager
    max_tool_iterations = 5  # Giới hạn số lần lặp để tránh vòng lặp vô tận

    # Lấy danh sách tool mà user có quyền truy cập
    accessible_tools = await tool_manager.get_accessible_tools(identity)
    chat_request.tools = accessible_tools

    current_messages = chat_request.messages

    for i in range(max_tool_iterations):
        with tracer.start_as_current_span(f"tool_loop_iteration_{i}") as loop_span:
            loop_span.set_attribute("iteration", i)
            
            # 1. Gọi LLM với message hiện tại
            body_dict = chat_request.model_dump()
            body_dict["messages"] = [msg.model_dump(exclude_none=True) for msg in current_messages]

            gateway_response = await request.app.state.router.execute_with_fallback(
                http_client=request.app.state.http_client,
                body=body_dict
            )

            choice = gateway_response.choices[0]
            message = choice.message

            # 2. Kiểm tra xem LLM có yêu cầu gọi tool không
            if choice.finish_reason != FinishReason.TOOL_CALLS or not message.tool_calls:
                # Nếu không, kết thúc vòng lặp và trả về kết quả
                with tracer.start_as_current_span("response_processing"):
                    final_content = ""
                    if isinstance(message.content, str):
                        final_content = message.content
                    elif isinstance(message.content, list):
                        final_content = " ".join(part.data.data for part in message.content if part.type == "text" and hasattr(part.data, 'data'))

                    safe_content = request.app.state.output_fillter.sanitize(final_content)
                    gateway_response.choices[0].message.content = safe_content
                    
                    gateway_metrics.metrics.increment_success()
                    latency = time.time() - start_time
                    gateway_metrics.metrics.record_latency(gateway_response.provider, gateway_response.model, latency)
                    return gateway_response

            # 3. Nếu có, thực thi các tool được yêu cầu
            logger.info("LLM requested tool calls", tool_calls=[tc.function.name for tc in message.tool_calls])
            
            # Thêm message của assistant (chứa yêu cầu gọi tool) vào lịch sử
            current_messages.append(message)

            tool_results: List[GatewayToolResult] = []
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                
                # Thực thi tool bằng GatewayToolManager
                result_content = await tool_manager.execute_tool(tool_name, tool_args, identity)
                
                tool_results.append(GatewayToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_name,
                    content=result_content
                ))
            
            # 4. Thêm message kết quả tool vào lịch sử và lặp lại
            current_messages.append(GatewayMessage(role="tool", tool_results=tool_results, content=""))

    raise HTTPException(status_code=500, detail="Tool execution loop exceeded maximum iterations.")


@router.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request, 
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint proxy thông minh xử lý Request kết nối đa mô hình (Multimodal Gateway)."""
    start_time = time.time()
    event_bus = request.app.state.event_bus

    # Phát sự kiện đầu tiên của luồng xử lý
    #await event_bus.publish(BaseEvent(
    #    event_name="gateway.request.received",
    #    payload={"path": request.url.path, "client": request.client.host}
    #))

    # --- Pipeline xử lý Request ---

    # 1. Đọc và kiểm tra cấu trúc dữ liệu đầu vào (Validation)
    chat_request = await parse_and_validate_request(request)
    # 2. Quản lý Session và Ngữ cảnh
    #context_engine: ContextEngine = request.app.state.context_manager
    #session: Session
    #SUMMARY_THRESHOLD = 10 # Tóm tắt sau mỗi 10 tin nhắn

    #if chat_request.session_id:
    #    try:
            # Tải toàn bộ ngữ cảnh, bao gồm cả session
    #        context_object = await context_engine.load_context(chat_request.session_id, identity)
    #        session = context_object.session
            # Nạp lại lịch sử hội thoại từ DB vào request hiện tại
    #        chat_request.messages = session.messages + chat_request.messages
    #        asyncio.create_task(event_bus.publish(BaseEvent(event_name="context.session.loaded", payload={"session_id": session.session_id})))
    #    except ValueError:
            # Nếu session không tồn tại, tạo mới
    #        logger.warning("Client provided non-existent session_id, creating a new one.", old_session_id=chat_request.session_id, user_id=identity.user_id)
    #        session = await context_engine.create_new_session(identity)
    #        asyncio.create_task(event_bus.publish(BaseEvent(
    #            event_name="chat.session.started",
    #            payload={"session_id": session.session_id, "user_id": identity.user_id}
    #        )))
    #else:
        # Nếu không có session_id, tạo session mới
    #    session = await context_engine.create_new_session(identity)
    #    asyncio.create_task(event_bus.publish(BaseEvent(
    #        event_name="chat.session.started",
    #        payload={"session_id": session.session_id, "user_id": identity.user_id}
    #    )))
    #chat_request.session_id = session.session_id # Gán lại session_id để các bước sau sử dụng
    
    # Kích hoạt sự kiện tóm tắt nếu cần
    #if len(session.messages) > 0 and len(session.messages) % SUMMARY_THRESHOLD == 0:
    #    await request.app.state.event_bus.publish(BaseEvent(event_name="session.summary.needed", payload={"session_id": session.session_id}))


    # 3. Bóc tách Prompt chính và sinh chuỗi khóa Cache định danh (Cache Key)
    #user_prompt, cache_key = extract_prompt_and_cache_key(chat_request)

    # 4. TỐI ƯU HÓA: Chạy song song các tác vụ không phụ thuộc (Guardrail & Rate Limit)
    #await asyncio.gather( # type: ignore
    #    run_input_guardrail(request, user_prompt),
    #    run_rate_limiter(request, identity)
    #)

    # 5. Kiểm tra Semantic Cache xem câu hỏi đã từng được trả lời chưa
    #cached_result = await request.app.state.cache.get(cache_key)
    #if cached_result:
    #    cached_response, _ = cached_result
    #    with tracer.start_as_current_span("process_cached_response"):
    #        gateway_metrics.metrics.increment_success() # type: ignore
    #        latency = time.time() - start_time
    #        gateway_metrics.metrics.record_latency("cache", "N/A", latency)
            
    #        safe_cached_response = request.app.state.output_fillter.sanitize(cached_response)
    #        return {"choices": [{"message": {"role": "assistant", "content": safe_cached_response}}]}

    #body_dict = chat_request.model_dump()

    try:
        if chat_request.config.stream:
            return StreamingResponse(
                generate_stream_response(request, chat_request, identity, start_time),
                media_type="text/event-stream"
            )
        else:
            return await handle_non_stream_response(request, chat_request, identity, start_time)
    except NoAvailableProviderError as e:
        trace.get_current_span().record_exception(e)
        logger.critical("All providers are unavailable", error=str(e))
        gateway_metrics.metrics.increment_failed(503)
        raise HTTPException(status_code=503, detail="Service Unavailable: All LLM providers are currently down.")