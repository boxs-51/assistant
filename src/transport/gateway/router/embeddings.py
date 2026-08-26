import structlog
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from ....domain.schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ..dependencies import get_container
from ....application.container import ApplicationContainer
from ....provider.exceptions import NoAvailableProviderError

router = APIRouter(prefix="/v1", tags=["LLM APIs"])
logger = structlog.get_logger(__name__)


@router.post("/embeddings")
async def embeddings_proxy(
    request: Request,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint để tạo vector embeddings cho văn bản.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    legacy_router = container.require("legacy_model_router")
    http_client = container.require("http_client")

    try:
        response_data = await legacy_router.execute_embeddings(
            http_client=http_client,
            body=body,
        )
        return JSONResponse(content=response_data)
    except NoAvailableProviderError as e:
        logger.critical("All providers are unavailable for embeddings", error=str(e))
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: All providers for embeddings are down.",
        )