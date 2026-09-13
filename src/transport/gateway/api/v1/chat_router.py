# src/transport/http/routes/chat.py
import time
import json
import uuid
import asyncio
import structlog
from typing import AsyncGenerator, Any

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .....domain.schemas import GatewayChatRequest
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from .....domain.schemas.event import BaseEvent
from ...dependencies import get_event_bus, get_config
from .....infrastructure.config.core import ConfigSchema
from .....infrastructure.event_bus.bus import EventBus


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
    request: Request, 
    identity: Identity = Depends(get_current_identity),
    event_bus: EventBus = Depends(get_event_bus),
    config: ConfigSchema = Depends(get_config)
):
    start_time = time.perf_counter()
    chat_request = await parse_and_validate_request(request)
    session_id = (
        chat_request.session_id
        if hasattr(chat_request, "session_id") and chat_request.session_id
        else str(uuid.uuid4())
    )
    is_stream = bool(chat_request.config and chat_request.config.stream)
    request_payload = chat_request.model_dump(exclude_none=True)
    identity_data = identity.model_dump() if hasattr(identity, "model_dump") else str(identity)

    # ------------------------------------------------------------------
    # 1. STREAMING RESPONSE VIA SSE BRIDGE
    # ------------------------------------------------------------------
    if is_stream:
        async def event_stream_bridge() -> AsyncGenerator[str, None]:
            queue: asyncio.Queue = asyncio.Queue()

            async def _on_chunk(evt: BaseEvent):
                if evt.session_id == session_id:
                    sse = evt.payload.get("sse")
                    if not sse:
                        chunk = evt.payload.get("chunk")
                        sse = f"data: {json.dumps(chunk)}\n\n" if chunk else None
                    if sse:
                        await queue.put(sse)

            async def _on_complete(evt: BaseEvent):
                if evt.session_id == session_id:
                    await queue.put("[DONE]")

            async def _on_fail(evt: BaseEvent):
                if evt.session_id == session_id:
                    await queue.put({"error": evt.payload.get("error", "Unknown stream error")})

            yield ": ping\n\n"

            try:
                event_bus.subscribe("provider.stream.chunk_emitted", _on_chunk)
                event_bus.subscribe("provider.stream.completed", _on_complete)
                event_bus.subscribe("provider.failed", _on_fail)

                event_bus.publish(
                    BaseEvent(
                        event_name="transport.event.request_received",
                        session_id=session_id,
                        payload={
                            "request_body": request_payload,
                            "identity": identity_data,
                        },
                    )
                )

                while True:
                    if await request.is_disconnected():
                        duration = round(time.perf_counter() - start_time, 4)
                        logger.warning(
                            "Client disconnected from SSE stream",
                            session_id=session_id,
                            duration_seconds=duration,
                        )
                        break

                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    if item == "[DONE]":
                        duration = round(time.perf_counter() - start_time, 4)
                        logger.info(
                            "Chat completion stream finished successfully",
                            session_id=session_id,
                            duration_seconds=duration,
                        )
                        yield "data: [DONE]\n\n"
                        break
                    elif isinstance(item, dict) and "error" in item:
                        duration = round(time.perf_counter() - start_time, 4)
                        logger.error(
                            "Chat completion stream failed",
                            session_id=session_id,
                            error=item.get("error"),
                            duration_seconds=duration,
                        )
                        yield f"data: {json.dumps(item)}\n\n"
                        yield "data: [DONE]\n\n"
                        break
                    else:
                        yield str(item) if str(item).startswith("data:") else f"data: {json.dumps(item)}\n\n"

            finally:
                event_bus.unsubscribe("provider.stream.chunk_emitted", _on_chunk)
                event_bus.unsubscribe("provider.stream.completed", _on_complete)
                event_bus.unsubscribe("provider.failed", _on_fail)

        return StreamingResponse(
            event_stream_bridge(), 
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    # ------------------------------------------------------------------
    # 2. NON-STREAMING RESPONSE VIA FUTURE WAIT
    # ------------------------------------------------------------------
    else:
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async def _on_response(evt: BaseEvent):
            if evt.session_id == session_id and not future.done():
                future.set_result(evt.payload)

        async def _on_failure(evt: BaseEvent):
            if evt.session_id == session_id and not future.done():
                future.set_exception(
                    HTTPException(
                        status_code=evt.payload.get("status_code", 500),
                        detail=evt.payload.get("error", "Provider execution failed"),
                    )
                )

        event_bus.subscribe("provider.chat.responded", _on_response)
        event_bus.subscribe("provider.failed", _on_failure)

        try:
            await event_bus.publish(
                BaseEvent(
                    event_name="transport.event.request_received",
                    session_id=session_id,
                    payload={
                        "request_body": request_payload,
                        "identity": identity_data,
                    },
                )
            )

            response_payload = await future

            duration = round(time.perf_counter() - start_time, 4)

            return response_payload.get("response", response_payload)

        except asyncio.TimeoutError:
            duration = round(time.perf_counter() - start_time, 4)
            logger.error(
                "Provider execution timed out",
                session_id=session_id,
                duration_seconds=duration,
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Provider execution timed out.",
            )
        except HTTPException as exc:
            duration = round(time.perf_counter() - start_time, 4)
            logger.error(
                "Chat completion execution failed with HTTP exception",
                session_id=session_id,
                status_code=exc.status_code,
                detail=exc.detail,
                duration_seconds=duration,
            )
            raise
        finally:
            event_bus.unsubscribe("provider.chat.responded", _on_response)
            event_bus.unsubscribe("provider.failed", _on_failure)