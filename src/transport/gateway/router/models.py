import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from ....application.container import ApplicationContainer
from ....domain.schemas.identity import Identity
from ....infrastructure.config import settings
from ..authentication.dependency import get_current_identity
from ..dependencies import get_container

router = APIRouter(prefix="/v1/models", tags=["Models"])
logger = structlog.get_logger(__name__)


@router.get("/")
async def list_models_proxy(
    provider_name: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint để lấy danh sách các model có sẵn từ một provider.
    """
    structlog.contextvars.bind_contextvars(provider_name=provider_name)

    try:
        legacy_router = container.require("legacy_model_router")
        http_client = container.require("http_client")

        provider = legacy_router.providers.get(provider_name)

        if not provider:
            raise HTTPException(
                status_code=404, detail=f"Provider '{provider_name}' not configured or found."
            )

        models_data = await provider.models.models(
            http_client=http_client, timeout=settings.provider.timeout
        )
        enriched_list = provider.capability_manager.enrich_capabilities(models_data)
        return enriched_list

    except NotImplementedError:
        logger.warning(
            "Requested model functionality not implemented for provider",
            provider=provider_name,
        )
        raise HTTPException(
            status_code=501,
            detail=f"The requested model functionality is not implemented for provider '{provider_name}'.",
        )
    except Exception as e:
        logger.error("Failed to process model request", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process model request for provider '{provider_name}'."
        )


@router.get("/{model_id:path}")
async def get_model_details_proxy(
    model_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp (e.g., gemini, openai)"),
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint để lấy thông tin chi tiết của một model cụ thể từ một provider.
    """
    structlog.contextvars.bind_contextvars(model_id=model_id, provider_name=provider_name)

    try:
        legacy_router = container.require("legacy_model_router")
        http_client = container.require("http_client")

        provider = legacy_router.providers.get(provider_name)

        if not provider:
            raise HTTPException(
                status_code=404, detail=f"Provider '{provider_name}' not configured or found."
            )

        model_data = await provider.models.model(
            http_client=http_client,
            timeout=settings.provider.timeout,
            model_name=model_id,
        )
        enriched_list = provider.capability_manager.enrich_capabilities(model_data)
        return enriched_list

    except NotImplementedError:
        logger.warning(
            "Get model details endpoint not implemented for provider",
            provider=provider_name,
        )
        raise HTTPException(
            status_code=501,
            detail=f"Fetching model details is not implemented for provider '{provider_name}'.",
        )
    except Exception as e:
        logger.error(
            "Failed to fetch model details", model=model_id, error=str(e), exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch model details from provider '{provider_name}'.",
        )