# authentication/manager.py
from starlette.requests import HTTPConnection
import structlog
from typing import List

from .exceptions import AuthenticationError, InvalidCredentialsError
from ....domain.schemas.identity import Identity
from .authenticators.base import AuthenticatorInterface

logger = structlog.get_logger(__name__)

# --- TEMPORARY AUTHENTICATION STRATEGY NOTE ---
# Tạm thời, đối với các máy khách không có tài khoản định danh trong hệ thống,
# chúng tôi sẽ sử dụng một JWT chung (guest pass JWT) có quyền hạn giới hạn.
#
# Hạn chế: Mô hình này có bảo mật thấp hơn (token chia sẻ, khó thu hồi cụ thể cho từng máy),
# và chỉ phù hợp cho các trường hợp truy cập không nhạy cảm.
#
# Kế hoạch tương lai: Cần xem xét và triển khai cơ chế xác thực máy-máy chuyên biệt
# (ví dụ: API keys được quản lý trong database với danh tính máy rõ ràng)
# để tăng cường bảo mật và khả năng quản lý khi có yêu cầu cao hơn.
# ---------------------------------------------
class AuthenticationManager:
    """
    Lớp điều phối chiến lược xác thực (Strategy Pattern).
    Nó duyệt qua một danh sách các 'authenticators' và sử dụng cái đầu tiên
    có thể xử lý định dạng token được cung cấp.
    """
    def __init__(self, authenticators: List[AuthenticatorInterface]):
        self.authenticators = authenticators

    async def authenticate(self, connection: HTTPConnection) -> Identity:
        auth_header = connection.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        elif hasattr(connection, "cookies"):
            token = connection.cookies.get("guest_access_token")
        if not token:
            raise InvalidCredentialsError("Missing or malformed Authorization header")

        for authenticator in self.authenticators:
            if authenticator.can_handle(token):
                return await authenticator.authenticate(token)

        raise InvalidCredentialsError("No authenticator available for the provided token format.")

    @staticmethod
    def has_credentials(connection: HTTPConnection) -> bool:
        auth_header = connection.headers.get("Authorization")
        return bool(
            (auth_header and auth_header.startswith("Bearer "))
            or (
                hasattr(connection, "cookies")
                and connection.cookies.get("guest_access_token")
            )
        )
