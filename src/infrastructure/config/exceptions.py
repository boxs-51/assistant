class ConfigError(Exception):
    """Lớp ngoại lệ cơ sở cho các lỗi cấu hình."""

class ConfigValidationError(ConfigError):
    """Lỗi khi xác thực schema cấu hình."""