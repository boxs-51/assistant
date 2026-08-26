# src/gateway/http/models_router.py
import uuid
import asyncio
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from typing import Any

from ..authentication.dependency import get_current_identity
from ....domain.schemas.identity import Identity
from ....domain.schemas.event import BaseEvent
from ....infrastructure.config import settings
from ..dependencies import get_event_bus

router = APIRouter(prefix="/v1/models", tags=["Models"])
logger = structlog.get_logger(__name__)

async def _dispatch_model_event(event_bus, payload: dict) -> Any:
    session_id = str(uuid.uuid4())
    future = asyncio.get_running_loop().create_future()

    async def _on_success(evt: BaseEvent):
        if evt.session_id == session_id and not future.done():
            future.set_result(evt.payload.get("result"))

    async def _on_failure(evt: BaseEvent):
        if evt.session_id == session_id and not future.done():
            future.set_exception(HTTPException(
                status_code=evt.payload.get("status_code", 500),
                detail=evt.payload.get("error", "Model operation failed.")
            ))

    event_bus.subscribe("provider.model.responded", _on_success)
    event_bus.subscribe("provider.failed", _on_failure)

    await event_bus.publish(BaseEvent(
        event_name="provider.model.execute",
        session_id=session_id,
        payload=payload
    ))

    try:
        return await asyncio.wait_for(future, timeout=settings.provider.timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Model query timed out.")


@router.get("/")
async def list_models_proxy(
    request: Request,
    provider_name: str,
    identity: Identity = Depends(get_current_identity),
    event_bus: Any = Depends(get_event_bus)
):
    structlog.contextvars.bind_contextvars(provider_name=provider_name)
    return await _dispatch_model_event(
        event_bus, 
        {"provider_name": provider_name}
    )


@router.get("/{model_id:path}")
async def get_model_details_proxy(
    request: Request,
    model_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp (e.g., gemini, openai)"),
    identity: Identity = Depends(get_current_identity),
    event_bus: Any = Depends(get_event_bus)
):
    structlog.contextvars.bind_contextvars(model_id=model_id, provider_name=provider_name)
    return await _dispatch_model_event(
        event_bus, 
        {"provider_name": provider_name, "model_id": model_id}
    )