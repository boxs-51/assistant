# src/transport/gateway/api/v1/chat_router.py
import time
import json
import uuid
import asyncio
import structlog
from typing import AsyncGenerator, Any

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .....domain.schemas import GatewayChatRequest
from .....domain.schemas.agent_execution import AgentExecutionLimits, MAX_AGENT_PROPOSED_TASK_SECONDS
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from .....domain.schemas.event import BaseEvent
from .....runtimes.agent.stream import (
    AGENT_STREAM_EVENT_NAMES,
    AgentStreamEvent,
    project_agent_event,
)
from ...dependencies import get_event_bus, get_config
from .....infrastructure.config.core import ConfigSchema
from .....infrastructure.event_bus.bus import EventBus
from .....application.container import ApplicationContainer
from .....application.connection_affinity import (
    ConnectionAffinityError,
    validate_connection_affinity,
)
from ...dependencies import get_container


router = APIRouter(tags=["LLM APIs Transport Layer"])
logger = structlog.get_logger(__name__)


_FAILURE_METADATA_FIELDS = (
    "error_code",
    "failure_domain",
    "retryable",
    "execution_id",
    "provider",
    "timeout_scope",
    "timeout_seconds",
)


_RESPONSE_TIMEOUT_POLL_SECONDS = 1.0


def _gateway_response_timeouts(config: ConfigSchema) -> tuple[float | None, float | None]:
    gateway = getattr(config, "gateway", None)
    if gateway is None:
        return None, None
    return (
        getattr(gateway, "response_idle_timeout_seconds", None),
        getattr(gateway, "response_hard_timeout_seconds", None),
    )


def _response_timeout_payload(
    *,
    error_code: str,
    timeout_scope: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    label = "idle" if timeout_scope == "response_idle" else "hard"
    return {
        "error": f"Gateway response {label} timeout expired.",
        "error_code": error_code,
        "timeout_scope": timeout_scope,
        "timeout_seconds": timeout_seconds,
    }


def _response_timeout_deadline(
    *,
    response_started_at: float,
    last_progress_at: float,
    response_idle_timeout_seconds: float | None,
    response_hard_timeout_seconds: float | None,
) -> tuple[float, str, str, float] | None:
    candidates: list[tuple[float, str, str, float]] = []
    if response_idle_timeout_seconds is not None:
        candidates.append(
            (
                last_progress_at + response_idle_timeout_seconds,
                "RESPONSE_IDLE_TIMEOUT",
                "response_idle",
                response_idle_timeout_seconds,
            )
        )
    if response_hard_timeout_seconds is not None:
        candidates.append(
            (
                response_started_at + response_hard_timeout_seconds,
                "RESPONSE_HARD_TIMEOUT",
                "response_hard",
                response_hard_timeout_seconds,
            )
        )
    return min(candidates, key=lambda item: item[0]) if candidates else None


def _provider_chunk_has_response_progress(chunk: Any) -> bool:
    if chunk is None:
        return False

    def get(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    for choice in get(chunk, "choices", []) or []:
        if get(choice, "finish_reason") is not None:
            return True
        delta = get(choice, "delta")
        if delta is None:
            continue
        if get(delta, "content"):
            return True
        if get(delta, "reasoning_content"):
            return True
        if get(delta, "tool_calls"):
            return True

    metadata = get(chunk, "metadata")
    return bool(get(metadata, "content_parts")) if metadata is not None else False


def _agent_stream_event_has_response_progress(event: AgentStreamEvent) -> bool:
    if event.channel == "tool":
        return True
    return bool(event.data.get("content") or event.data.get("tool_calls"))


def _consume_background_dispatch_result(future: asyncio.Future) -> None:
    if future.cancelled():
        return
    try:
        error = future.exception()
    except Exception:
        logger.warning(
            "Gateway background request dispatch result could not be read",
            exc_info=True,
        )
        return
    if error is not None:
        logger.warning(
            "Gateway request dispatch failed after response lifecycle ended",
            error=str(error),
            error_type=type(error).__name__,
        )


def _transport_failure_payload(payload: dict[str, Any], *, default_error: str) -> dict[str, Any]:
    result = {"error": payload.get("error", default_error)}
    for field in _FAILURE_METADATA_FIELDS:
        if field in payload:
            result[field] = payload[field]
    return result


def _transport_failure_detail(payload: dict[str, Any]) -> str | dict[str, Any]:
    result = _transport_failure_payload(
        payload,
        default_error="Provider execution failed",
    )
    # Keep the legacy non-streaming detail string when no structured metadata
    # exists. R2.1 only widens the wire shape for classified failures.
    return result["error"] if len(result) == 1 else result


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
    config: ConfigSchema = Depends(get_config),
    container: ApplicationContainer = Depends(get_container),
):
    start_time = time.perf_counter()
    chat_request = await parse_and_validate_request(request)
    connection_snapshot = None
    if chat_request.connection_id:
        try:
            connection_snapshot = validate_connection_affinity(
                container.connection_runtime.registry,
                chat_request.connection_id,
                identity.user_id or "",
            )
        except ConnectionAffinityError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={"code": error.code, "message": error.message},
            ) from error
    if connection_snapshot is not None:
        chat_request.metadata.routing["client_id"] = (
            connection_snapshot.metadata.get("client_id")
        )
    session_id = (
        chat_request.session_id
        if hasattr(chat_request, "session_id") and chat_request.session_id
        else str(uuid.uuid4())
    )
    turn_id = f"turn_{uuid.uuid4().hex}"
    is_stream = bool(chat_request.config and chat_request.config.stream)
    agent_activity_stream = bool(
        is_stream
        and chat_request.agent_enabled
        and chat_request.config.agent_activity_stream
    )
    request_payload = chat_request.model_dump(exclude_none=True)
    request_payload["_chat_execution_mode"] = chat_request.execution_mode.value
    identity_data = identity.model_dump() if hasattr(identity, "model_dump") else str(identity)

    # ------------------------------------------------------------------
    # 1. STREAMING RESPONSE VIA SSE BRIDGE
    # ------------------------------------------------------------------
    if is_stream:
        async def event_stream_bridge() -> AsyncGenerator[str, None]:
            queue: asyncio.Queue = asyncio.Queue()
            (
                response_idle_timeout_seconds,
                response_hard_timeout_seconds,
            ) = _gateway_response_timeouts(config)
            response_started_at = time.monotonic()
            last_progress_at = response_started_at

            async def _on_chunk(evt: BaseEvent):
                if evt.session_id == session_id and evt.turn_id == turn_id:
                    chunk = evt.payload.get("chunk")
                    sse = evt.payload.get("sse")
                    if not sse:
                        sse = f"data: {json.dumps(chunk)}\n\n" if chunk else None
                    if sse:
                        arrived_at = time.monotonic()
                        await queue.put(
                            (
                                "frame",
                                sse,
                                _provider_chunk_has_response_progress(chunk),
                                arrived_at,
                            )
                        )

            async def _on_complete(evt: BaseEvent):
                if evt.session_id == session_id and evt.turn_id == turn_id:
                    await queue.put(("done", None, False, time.monotonic()))

            async def _on_fail(evt: BaseEvent):
                if evt.session_id == session_id and evt.turn_id == turn_id:
                    await queue.put(
                        (
                            "error",
                            _transport_failure_payload(
                                evt.payload,
                                default_error="Unknown stream error",
                            ),
                            False,
                            time.monotonic(),
                        )
                    )

            async def _on_agent_event(evt: BaseEvent):
                if evt.session_id != session_id or evt.turn_id != turn_id:
                    return
                arrived_at = time.monotonic()
                projected = project_agent_event(evt)
                await queue.put(
                    (
                        "frame",
                        f"data: {projected.model_dump_json(exclude_none=True)}\n\n",
                        _agent_stream_event_has_response_progress(projected),
                        arrived_at,
                    )
                )

            yield ": ping\n\n"
            last_heartbeat = time.monotonic()

            try:
                event_bus.subscribe("provider.stream.chunk_emitted", _on_chunk)
                event_bus.subscribe("provider.stream.completed", _on_complete)
                event_bus.subscribe("provider.failed", _on_fail)
                if agent_activity_stream:
                    for event_name in AGENT_STREAM_EVENT_NAMES:
                        event_bus.subscribe(event_name, _on_agent_event)

                event_bus.publish(
                    BaseEvent(
                        event_name="transport.event.request_received",
                        session_id=session_id,
                        turn_id=turn_id,
                        payload={
                            "request_body": request_payload,
                            "identity": identity_data,
                            "turn_id": turn_id,
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

                    queued_item = None
                    try:
                        queued_item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                    if queued_item is None:
                        now = time.monotonic()
                        deadline = _response_timeout_deadline(
                            response_started_at=response_started_at,
                            last_progress_at=last_progress_at,
                            response_idle_timeout_seconds=response_idle_timeout_seconds,
                            response_hard_timeout_seconds=response_hard_timeout_seconds,
                        )
                        wait_timeout = _RESPONSE_TIMEOUT_POLL_SECONDS
                        if deadline is not None:
                            wait_timeout = min(
                                wait_timeout,
                                max(0.0, deadline[0] - now),
                            )

                        if wait_timeout > 0:
                            try:
                                queued_item = await asyncio.wait_for(
                                    queue.get(),
                                    timeout=wait_timeout,
                                )
                            except asyncio.TimeoutError:
                                pass

                        if queued_item is None:
                            try:
                                queued_item = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass

                    if queued_item is None:
                        now = time.monotonic()
                        deadline = _response_timeout_deadline(
                            response_started_at=response_started_at,
                            last_progress_at=last_progress_at,
                            response_idle_timeout_seconds=response_idle_timeout_seconds,
                            response_hard_timeout_seconds=response_hard_timeout_seconds,
                        )
                        if deadline is not None and deadline[0] <= now:
                            _, error_code, timeout_scope, timeout_seconds = deadline
                            payload = _response_timeout_payload(
                                error_code=error_code,
                                timeout_scope=timeout_scope,
                                timeout_seconds=timeout_seconds,
                            )
                            logger.warning(
                                "Gateway streaming response timeout expired",
                                session_id=session_id,
                                error_code=error_code,
                                timeout_scope=timeout_scope,
                                timeout_seconds=timeout_seconds,
                            )
                            yield f"data: {json.dumps(payload)}\n\n"
                            yield "data: [DONE]\n\n"
                            break
                        if now - last_heartbeat >= 15:
                            yield ": ping\n\n"
                            last_heartbeat = now
                        continue

                    item_kind, item, meaningful_progress, item_arrived_at = queued_item
                    deadline = _response_timeout_deadline(
                        response_started_at=response_started_at,
                        last_progress_at=last_progress_at,
                        response_idle_timeout_seconds=response_idle_timeout_seconds,
                        response_hard_timeout_seconds=response_hard_timeout_seconds,
                    )
                    if deadline is not None and item_arrived_at > deadline[0]:
                        _, error_code, timeout_scope, timeout_seconds = deadline
                        payload = _response_timeout_payload(
                            error_code=error_code,
                            timeout_scope=timeout_scope,
                            timeout_seconds=timeout_seconds,
                        )
                        logger.warning(
                            "Gateway streaming response timeout expired before queued item",
                            session_id=session_id,
                            error_code=error_code,
                            timeout_scope=timeout_scope,
                            timeout_seconds=timeout_seconds,
                            item_kind=item_kind,
                            item_arrived_at=item_arrived_at,
                        )
                        yield f"data: {json.dumps(payload)}\n\n"
                        yield "data: [DONE]\n\n"
                        break

                    if item_kind == "done":
                        duration = round(time.perf_counter() - start_time, 4)
                        logger.info(
                            "Chat completion stream finished successfully",
                            session_id=session_id,
                            duration_seconds=duration,
                        )
                        yield "data: [DONE]\n\n"
                        break
                    if item_kind == "error":
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

                    now = time.monotonic()
                    last_heartbeat = now
                    if meaningful_progress:
                        last_progress_at = item_arrived_at
                    yield item

            finally:
                event_bus.unsubscribe("provider.stream.chunk_emitted", _on_chunk)
                event_bus.unsubscribe("provider.stream.completed", _on_complete)
                event_bus.unsubscribe("provider.failed", _on_fail)
                if agent_activity_stream:
                    for event_name in AGENT_STREAM_EVENT_NAMES:
                        event_bus.unsubscribe(event_name, _on_agent_event)

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
            if evt.session_id == session_id and evt.turn_id == turn_id and not future.done():
                future.set_result(evt.payload)

        async def _on_failure(evt: BaseEvent):
            if evt.session_id == session_id and evt.turn_id == turn_id and not future.done():
                future.set_exception(
                    HTTPException(
                        status_code=evt.payload.get("status_code", 500),
                        detail=_transport_failure_detail(evt.payload),
                    )
                )

        event_bus.subscribe("provider.chat.responded", _on_response)
        event_bus.subscribe("provider.failed", _on_failure)

        legacy_timeout_val = config.provider.timeout or 60
        if chat_request.agent_enabled:
            limits = chat_request.agent_limits or AgentExecutionLimits()
            task_cap = (
                limits.task_timeout_seconds
                or (limits.timeout_seconds if chat_request.agent_limits else MAX_AGENT_PROPOSED_TASK_SECONDS)
            )
            legacy_timeout_val = max(legacy_timeout_val, task_cap + 5)

        (
            response_idle_timeout_seconds,
            response_hard_timeout_seconds,
        ) = _gateway_response_timeouts(config)
        canonical_response_timeout_enabled = (
            response_idle_timeout_seconds is not None
            or response_hard_timeout_seconds is not None
        )
        timeout_candidates = []
        if response_idle_timeout_seconds is not None:
            timeout_candidates.append(
                (
                    response_idle_timeout_seconds,
                    "RESPONSE_IDLE_TIMEOUT",
                    "response_idle",
                )
            )
        if response_hard_timeout_seconds is not None:
            timeout_candidates.append(
                (
                    response_hard_timeout_seconds,
                    "RESPONSE_HARD_TIMEOUT",
                    "response_hard",
                )
            )
        if not timeout_candidates:
            timeout_candidates.append(
                (
                    legacy_timeout_val,
                    "GATEWAY_RESPONSE_TIMEOUT",
                    "response_wait",
                )
            )
        timeout_val, timeout_error_code, timeout_scope = min(
            timeout_candidates,
            key=lambda item: item[0],
        )

        request_event = BaseEvent(
            event_name="transport.event.request_received",
            session_id=session_id,
            turn_id=turn_id,
            payload={
                "request_body": request_payload,
                "identity": identity_data,
                "turn_id": turn_id,
            },
        )

        try:
            if canonical_response_timeout_enabled:
                async def _dispatch_and_wait_for_response() -> dict[str, Any]:
                    dispatch_future = asyncio.ensure_future(
                        event_bus.publish(request_event)
                    )
                    try:
                        while True:
                            done, _ = await asyncio.wait(
                                {dispatch_future, future},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if future in done:
                                response_error = (
                                    None
                                    if future.cancelled()
                                    else future.exception()
                                )
                                if response_error is not None:
                                    return future.result()
                                await dispatch_future
                                return future.result()
                            dispatch_future.result()
                            return await future
                    finally:
                        if not dispatch_future.done():
                            dispatch_future.add_done_callback(
                                _consume_background_dispatch_result
                            )

                response_payload = await asyncio.wait_for(
                    _dispatch_and_wait_for_response(),
                    timeout=timeout_val,
                )
            else:
                # Compatibility path: preserve the pre-UBQ-5D sequencing when
                # canonical response timeout settings are not configured.
                await event_bus.publish(request_event)
                response_payload = await asyncio.wait_for(
                    future,
                    timeout=timeout_val,
                )

            duration = round(time.perf_counter() - start_time, 4)

            response_body = response_payload.get("response", response_payload)
            http_status = int(response_payload.get("_http_status", 200))
            if http_status != 200:
                return JSONResponse(status_code=http_status, content=response_body)
            return response_body

        except asyncio.TimeoutError:
            duration = round(time.perf_counter() - start_time, 4)
            logger.error(
                "Gateway response wait timed out",
                session_id=session_id,
                error_code=timeout_error_code,
                timeout_scope=timeout_scope,
                duration_seconds=duration,
            )
            if timeout_error_code == "GATEWAY_RESPONSE_TIMEOUT":
                detail = {
                    "error": "Gateway response wait timed out.",
                    "error_code": timeout_error_code,
                    "timeout_scope": timeout_scope,
                    "timeout_seconds": timeout_val,
                }
            else:
                detail = _response_timeout_payload(
                    error_code=timeout_error_code,
                    timeout_scope=timeout_scope,
                    timeout_seconds=timeout_val,
                )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=detail,
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
            if canonical_response_timeout_enabled and not future.done():
                future.cancel()
            event_bus.unsubscribe("provider.chat.responded", _on_response)
            event_bus.unsubscribe("provider.failed", _on_failure)
