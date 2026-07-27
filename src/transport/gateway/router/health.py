from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import psutil
from prometheus_client import generate_latest

from ....infrastructure.config import settings

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    """Endpoint đơn giản cho Kubernetes liveness probe."""
    return {"status": "ok"}

@router.get("/ready")
async def readiness_check(request: Request):
    """
    Kiểm tra sự sẵn sàng của các dịch vụ phụ thuộc (Redis, LLM Providers).
    Sử dụng cho Kubernetes readiness probe.
    """
    try:
        # 1. Kiểm tra Redis
        await request.app.state.storage.drivers.get("redis").ping()
        
        # 2. Kiểm tra các provider đã cấu hình
        # Gửi một request nhỏ, không tốn kém để kiểm tra kết nối
        for provider_name, provider in request.app.state.router.providers.items():
            # Ví dụ: OpenAI có endpoint /v1/models để kiểm tra
            if provider_name == "openai":
                 await request.app.state.http_client.get(f"{provider.api_url}/models", headers=provider.headers)

    except Exception as e:
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