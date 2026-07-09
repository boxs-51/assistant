from fastapi import Request, HTTPException, status, Depends
from ..schemas.identity import Identity

def get_current_identity(request: Request) -> Identity:
    """
    FastAPI Dependency để lấy Identity object đã được xác thực từ middleware.
    Đây là "Single Source of Truth" cho các tầng phía sau.
    """
    identity = getattr(request.state, "identity", None)
    if not identity:
        # Lỗi này chỉ xảy ra nếu middleware bị cấu hình sai hoặc request không đi qua middleware
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return identity

def require_permission(permission: str):
    """
    Dependency Factory: Tạo một dependency để yêu cầu một quyền hạn cụ thể.
    """
    def dependency(identity: Identity = Depends(get_current_identity)) -> Identity:
        if permission not in identity.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Required permission '{permission}' is missing."
            )
        return identity
    return dependency