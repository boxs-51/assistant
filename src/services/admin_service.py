from typing import Dict, Any, List
import structlog
from fastapi import Request

logger = structlog.get_logger(__name__)

class AdminService:
    """Service điều phối các thao tác Quản trị hệ thống."""

    def __init__(self, request: Request):
        # Lấy trực tiếp router / runtime instances từ app state
        self.routing_policy = getattr(request.app.state, "router", None) and getattr(
            request.app.state.router, "routing_policy", None
        )
        self.circuit_breaker_manager = getattr(
            request.app.state, "router", None
        ) and getattr(request.app.state.router, "circuit_breaker_manager", None)

    async def reload_routing_rules(self) -> bool:
        """Tải lại nóng các quy tắc định tuyến."""
        if not self.routing_policy:
            logger.error("Routing policy engine not initialized in app.state")
            return False
        
        try:
            return await self.routing_policy.reload_rules()
        except Exception as e:
            logger.exception("Error during routing rules hot-reload", error=str(e))
            return False

    async def get_circuit_breaker_statuses(self) -> Dict[str, Any]:
        """Lấy danh sách trạng thái của toàn bộ Circuit Breakers."""
        if not self.circuit_breaker_manager:
            logger.error("Circuit Breaker Manager not initialized in app.state")
            return {}

        return await self.circuit_breaker_manager.get_all_statuses()