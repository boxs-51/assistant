import psutil
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest

from ....application.container import ApplicationContainer
from ....infrastructure.config import settings
from ..dependencies import get_container

router = APIRouter(tags=["Health"])
logger = structlog.get_logger(__name__)


@router.get("/health")
async def health_check():
    """Endpoint đơn giản cho Kubernetes liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check(
    container: ApplicationContainer = Depends(get_container),
):
    """
    Kiểm tra sự sẵn sàng của các dịch vụ phụ thuộc (Redis, LLM Providers).
    Sử dụng cho Kubernetes readiness probe.
    """
    try:
        # 1. Kiểm tra Redis
        redis_driver = container.storage.drivers.get("redis")
        if redis_driver:
            await redis_driver.ping()

        # 2. Kiểm tra các provider đã cấu hình
        legacy_router = container.require("legacy_model_router")
        http_client = container.require("http_client")

        for provider_name, provider in legacy_router.providers.items():
            # Ví dụ: OpenAI có endpoint /v1/models để kiểm tra
            if provider_name == "openai":
                await http_client.get(f"{provider.api_url}/models", headers=provider.headers)

    except Exception as e:
        logger.error("Readiness check failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {str(e)}")

    return {"status": "ready"}


@router.get("/metrics")
def get_metrics():
    """Expose các số liệu cho Prometheus scrape."""
    return StreamingResponse(generate_latest(), media_type="text/plain")


@router.get("/stats")
async def get_stats():
    """Cung cấp thống kê hoạt động ở dạng JSON cho dashboard nội bộ."""
    process = psutil.Process()
    return {
        "gateway_name": settings.gateway.name,
        "gateway_version": settings.gateway.version,
        "cpu_usage_percent": process.cpu_percent(interval=0.1),
        "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
    }