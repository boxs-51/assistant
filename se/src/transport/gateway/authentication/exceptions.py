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

class OTPCooldownError(AuthenticationError):
    """Ném ra khi người dùng yêu cầu gửi lại OTP quá nhanh (chưa hết thời gian chờ)."""
    def __init__(self, remaining_seconds: int, detail: str = "Please wait before requesting a new OTP"):
        self.remaining_seconds = remaining_seconds
        super().__init__(f"{detail}. Cooldown remaining: {remaining_seconds}s")

class OTPInvalidError(AuthenticationError):
    """Ném ra khi mã OTP nhập sai hoặc đã hết hạn."""
    def __init__(self, detail: str = "Invalid or expired OTP code"):
        super().__init__(detail)