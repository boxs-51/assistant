class AuthenticationError(Exception):
    """Lớp exception cơ sở cho các lỗi xác thực."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)

class InvalidCredentialsError(AuthenticationError):
    """Ném ra khi thông tin đăng nhập (email/password, token) không hợp lệ."""
    def __init__(self, detail: str = "Invalid credentials provided"):
        super().__init__(detail)

class PermissionDeniedError(AuthenticationError):
    """Ném ra khi người dùng đã xác thực nhưng không có quyền thực hiện hành động."""
    def __init__(self, detail: str = "You do not have permission to perform this action"):
        super().__init__(detail)