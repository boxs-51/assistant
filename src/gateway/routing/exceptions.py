class ProviderError(Exception):
    """Lớp ngoại lệ cơ sở cho tất cả các lỗi liên quan đến provider."""
    def __init__(self, message, provider_name=None):
        self.provider_name = provider_name
        super().__init__(f"[{provider_name}] {message}" if provider_name else message)

class NoAvailableProviderError(ProviderError):
    """Ngoại lệ được ném ra khi tất cả các provider trong chuỗi fallback đều thất bại."""
    pass

class ProviderAuthenticationError(ProviderError):
    """Lỗi xác thực với provider (e.g., sai API key)."""
    pass

class ProviderRateLimitError(ProviderError):
    """Lỗi do vượt quá giới hạn tần suất của provider."""
    pass

class ProviderUnavailableError(ProviderError):
    """Lỗi khi provider không khả dụng (e.g., 503 Service Unavailable)."""
    pass

class ResponseValidationError(ProviderError):
    """Lỗi khi phản hồi từ provider không hợp lệ (e.g., sai schema JSON)."""
    pass