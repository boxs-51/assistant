from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..authentication.dependency import require_permission, verify_admin_ip
from ..dependencies import get_container
from ....application.container import ApplicationContainer

router = APIRouter(
    prefix="/admin", 
    tags=["Admin"],
    dependencies=[
        Depends(verify_admin_ip),                  # Lớp 1: Kiểm tra IP Whitelist
        Depends(require_permission("admin:write"))  # Lớp 2: Kiểm tra Token (Quyền hạn của JWT hoặc ak_)
    ]
)


@router.post(
    "/reload/routing",
    dependencies=[Depends(require_permission("admin:write"))]
)
async def reload_routing_rules(
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint quản trị để tải lại nóng (hot-reload) các quy tắc định tuyến từ file YAML.
    Yêu cầu xác thực.
    """
    legacy_router = container.require("legacy_model_router")
    success = await legacy_router.routing_policy.reload_rules()
    if success:
        return {"status": "success", "message": "Routing rules reloaded successfully."}
    else:
        raise HTTPException(status_code=500, detail="Failed to reload routing rules. Check logs for details.")


@router.get(
    "/circuit-breakers/status",
    dependencies=[Depends(require_permission("admin:read"))]
)
async def get_circuit_breaker_statuses(
    container: ApplicationContainer = Depends(get_container),
):
    """
    Endpoint quản trị để xem trạng thái hiện tại của tất cả các Circuit Breaker.
    Cung cấp thông tin chi tiết về trạng thái (open, closed, half-open),
    số lỗi, và thời gian xảy ra lỗi cuối cùng.
    Yêu cầu xác thực.
    """
    circuit_breaker_manager = container.require("circuit_breaker_manager")
    statuses = await circuit_breaker_manager.get_all_statuses()
    return JSONResponse(content=statuses)