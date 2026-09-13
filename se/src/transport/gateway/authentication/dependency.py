# authentication/dependency.py
from fastapi import Request, HTTPException, status, Depends
from typing import Any
from ....domain.schemas.identity import Identity
from ....infrastructure.config import ConfigSchema
from ....application import ApplicationContainer
from ...gateway.dependencies import get_config, get_container


# Lấy danh sách IP được phép từ file cấu hình (Mặc định cho phép localhost nếu không cấu hình)

def get_api_key_service(
    container: ApplicationContainer = Depends(get_container)
) -> Any:
    """FastAPI Dependency để lấy APIKeyService."""
    return container.require("api_key_service")


async def verify_admin_ip(request: Request, config: ConfigSchema = Depends(get_config)):
    """
    Dependency kiểm tra IP Client có thuộc danh sách Whitelist cho Admin hay không.
    """
    # Xử lý trường hợp có proxy đứng trước (như Nginx, Cloudflare)
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else None
    ALLOWED_ADMIN_IPS = getattr(config.auth.admin_ips, "allowed_ips", ["127.0.0.1", "::1"])
    if not client_ip or client_ip not in ALLOWED_ADMIN_IPS:
        import structlog
        logger = structlog.get_logger(__name__)
        logger.warning("Unauthorized Admin IP block triggered", unauthorized_ip=client_ip, path=request.url.path)
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Your IP address is not authorized to access administrative resources."
        )

def get_current_identity(request: Request) -> Identity:
    """
    FastAPI Dependency để lấy Identity object đã được xác thực từ middleware.
    """
    identity = getattr(request.state, "identity", None)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return identity

def require_permission(permission: str):
    """
    Dependency Factory: Yêu cầu một quyền hạn cụ thể.
    """
    def dependency(identity: Identity = Depends(get_current_identity)) -> Identity:
        if permission not in identity.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Required permission '{permission}' is missing."
            )
        return identity
    return dependency