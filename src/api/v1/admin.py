from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from ...transport.gateway.authentication.dependency import require_permission, verify_admin_ip
from ...services.admin_service import AdminService

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[
        Depends(verify_admin_ip),                  # Lớp 1: IP Whitelist
        Depends(require_permission("admin:write"))  # Lớp 2: Permission mặc định cho nhánh Admin
    ]
)


@router.post(
    "/reload/routing",
    summary="Hot-reload quy tắc định tuyến",
    description="Tải lại các rules từ YAML mà không cần khởi động lại Gateway."
)
async def reload_routing_rules(admin_service: AdminService = Depends()):
    """Endpoint reload routing policy."""
    success = await admin_service.reload_routing_rules()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reload routing rules. Check system logs for details."
        )
        
    return {"status": "success", "message": "Routing rules reloaded successfully."}


@router.get(
    "/circuit-breakers/status",
    summary="Trạng thái Circuit Breakers",
    dependencies=[Depends(require_permission("admin:read"))]  # Ghi đè chỉ yêu cầu admin:read cho endpoint này
)
async def get_circuit_breaker_statuses(admin_service: AdminService = Depends()):
    """Endpoint kiểm tra trạng thái Circuit Breaker."""
    statuses = await admin_service.get_circuit_breaker_statuses()
    return JSONResponse(content=statuses)