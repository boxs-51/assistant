import psutil
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest

from .....application.container import ApplicationContainer
from ...dependencies import get_container

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check(
    container: ApplicationContainer = Depends(get_container),
):
    """Kubernetes readiness probe backed by the application dependency graph."""
    try:
        redis_driver = container.storage.drivers.get("redis")
        if redis_driver:
            await redis_driver.ping()

        provider_runtime = container.provider_runtime
        if provider_runtime is None or not provider_runtime.providers:
            raise RuntimeError("Provider runtime is not initialized or has no providers.")

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: {exc}",
        ) from exc

    return {"status": "ready"}


@router.get("/metrics")
def get_metrics():
    """Prometheus metrics scraper."""
    return StreamingResponse(generate_latest(), media_type="text/plain")


@router.get("/stats")
async def get_stats(
    container: ApplicationContainer = Depends(get_container),
):
    process = psutil.Process()
    config = container.config
    return {
        "gateway_name": config.gateway.name,
        "gateway_version": config.gateway.version,
        "cpu_usage_percent": process.cpu_percent(interval=0.1),
        "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
    }
