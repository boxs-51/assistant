# src/gateway/http/chat_router.py
import time
import json
import uuid
import asyncio
import structlog
from typing import AsyncGenerator

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ....domain.schemas import GatewayChatRequest, GatewayResponse
from ....domain.schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ....domain.schemas.event import BaseEvent
from ....infrastructure.config import settings

router = APIRouter(tags=["LLM APIs Transport Layer"])
logger = structlog.get_logger(__name__)


async def parse_and_validate_request(request: Request) -> GatewayChatRequest:
    try:
        raw_body = await request.json()
        return GatewayChatRequest(**raw_body)
    except ValidationError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Invalid request schema", "errors": val_err.errors()},
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON.")


@router.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request, identity: Identity = Depends(get_current_identity)
):
    """
    HTTP Adapter Endpoint:
    Đóng vai trò là Transport Layer - Nhận request, phát Event sang Runtime, và trả phản hồi.
    """
    start_time = time.time()
    chat_request = await parse_and_validate_request(request)
    session_id = chat_request.session_id if hasattr(chat_request, "session_id") and chat_request.session_id else str(uuid.uuid4())
    
    event_bus = request.app.state.event_bus
    is_stream = bool(chat_request.config and chat_request.config.stream)

    # 1. STREAMING RESPONSE VIA EVENT BRIDGE
    if is_stream:
        async def event_stream_bridge() -> AsyncGenerator[str, None]:
            queue: asyncio.Queue = asyncio.Queue()

            # Tự đăng ký handler tạm thời nhận Stream Chunk cho Session này
            async def _on_chunk(evt: BaseEvent):
                if evt.session_id == session_id:
                    await queue.put(evt.payload.get("sse"))

            async def _on_complete(evt: BaseEvent):
                if evt.session_id == session_id:
                    await queue.put("[DONE]")

            async def _on_fail(evt: BaseEvent):
                if evt.session_id == session_id:
                    await queue.put({"error": evt.payload.get("error")})

            event_bus.subscribe("ProviderStreamChunk", _on_chunk)
            event_bus.subscribe("ProviderStreamCompleted", _on_complete)
            event_bus.subscribe("ProviderFailed", _on_fail)

            # Phát Event sang Provider Runtime yêu cầu bắt đầu xử lý
            await event_bus.publish(BaseEvent(
                event_name="ExecuteProvider",
                session_id=session_id,
                payload={"request_body": chat_request.model_dump(exclude_none=True), "is_stream": True}
            ))

            while True:
                item = await queue.get()
                if item == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break
                elif isinstance(item, dict) and "error" in item:
                    yield f"data: {json.dumps(item)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                else:
                    yield str(item)

        return StreamingResponse(event_stream_bridge(), media_type="text/event-stream")

    # 2. NON-STREAMING RESPONSE VIA EVENT WAIT
    else:
        future = asyncio.get_running_loop().create_future()

        async def _on_response(evt: BaseEvent):
            if evt.session_id == session_id and not future.done():
                future.set_result(evt.payload)

        async def _on_failure(evt: BaseEvent):
            if evt.session_id == session_id and not future.done():
                future.set_exception(HTTPException(
                    status_code=evt.payload.get("status_code", 500),
                    detail=evt.payload.get("error", "Provider execution failed")
                ))

        event_bus.subscribe("ProviderResponded", _on_response)
        event_bus.subscribe("ProviderFailed", _on_failure)

        # Trực tiếp phát BaseEvent sang Runtime Kernel
        await event_bus.publish(BaseEvent(
            event_name="ExecuteProvider",
            session_id=session_id,
            payload={"request_body": chat_request.model_dump(exclude_none=True), "is_stream": False}
        ))

        try:
            payload = await asyncio.wait_for(future, timeout=settings.provider.timeout)
            return payload["response"]
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Provider execution timed out.")