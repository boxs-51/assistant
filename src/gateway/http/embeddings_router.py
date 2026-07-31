# src/gateway/http/embeddings_router.py
import uuid
import asyncio
import structlog
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from ...schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ...kernel.base import Event
from ...config import settings

router = APIRouter(prefix="/v1", tags=["LLM APIs"])
logger = structlog.get_logger(__name__)

@router.post("/embeddings")
async def embeddings_proxy(
    request: Request, 
    identity: Identity = Depends(get_current_identity)
):
    """
    HTTP Transport Adapter cho Vector Embeddings.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    session_id = str(uuid.uuid4())
    event_bus = request.app.state.event_bus
    future = asyncio.get_running_loop().create_future()

    async def _on_responded(evt: Event):
        if evt.session_id == session_id and not future.done():
            future.set_result(evt.payload.get("response"))

    async def _on_failure(evt: Event):
        if evt.session_id == session_id and not future.done():
            future.set_exception(HTTPException(
                status_code=evt.payload.get("status_code", 500),
                detail=evt.payload.get("error", "Embeddings execution failed")
            ))

    event_bus.subscribe("EmbeddingsResponded", _on_responded)
    event_bus.subscribe("ProviderFailed", _on_failure)

    # Phát Event yêu cầu Runtime thực thi Embeddings
    await event_bus.publish(Event(
        event_name="ExecuteEmbeddings",
        session_id=session_id,
        payload={"request_body": body}
    ))

    try:
        response_data = await asyncio.wait_for(future, timeout=settings.provider.timeout)
        return JSONResponse(content=response_data)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Embeddings request timed out.")