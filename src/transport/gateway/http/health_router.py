# src/gateway/http/health_router.py
import psutil
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest

from ...config import settings

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    """Kubernetes liveness probe."""
    return {"status": "ok"}

@router.get("/ready")
async def readiness_check(request: Request):
    """
    Kubernetes readiness probe.
    Kiểm tra trạng thái sẵn sàng thông qua Lifecycle / Event System.
    """
    try:
        # Lấy danh sách các Runtimes đã được khởi tạo thành công từ Lifecycle Manager
        lifecycle_manager = getattr(request.app.state, "lifecycle_manager", None)
        if lifecycle_manager and not lifecycle_manager.is_fully_booted():
            raise HTTPException(status_code=503, detail="System runtime is still booting.")

        # Kiểm tra ping Redis thông qua Storage Runtime / Driver
        storage_driver = request.app.state.storage.drivers.get("redis")
        if storage_driver:
            await storage_driver.ping()

    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {str(e)}")

    return {"status": "ready"}

@router.get("/metrics")
def get_metrics():
    """Prometheus metrics scraper."""
    return StreamingResponse(generate_latest(), media_type="text/plain")

@router.get("/stats")
async def get_stats():
    """Thống kê tài nguyên hệ thống."""
    process = psutil.Process()
    return {
        "gateway_name": settings.gateway.name,
        "gateway_version": settings.gateway.version,
        "cpu_usage_percent": process.cpu_percent(interval=0.1),
        "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
    }