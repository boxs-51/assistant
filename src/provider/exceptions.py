from typing import Optional, Any

class ProviderError(Exception):
    """Lớp ngoại lệ cơ sở cho tất cả các lỗi liên quan đến provider."""
    def __init__(
        self, 
        message: str, 
        provider_name: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        raw_response: Optional[Any] = None,
        is_network_error: bool = False
    ):
        self.provider_name = provider_name
        self.status_code = status_code
        self.error_code = error_code
        self.raw_response = raw_response
        self.is_network_error = is_network_error
        
        # Tạo message chi tiết bao gồm cả mã lỗi nếu có
        prefix = f"[{provider_name}]" if provider_name else ""
        if is_network_error:
            code_info = " (Network/Connection Error)"
        else:
            code_info = f" (Status: {status_code}, Code: {error_code})" if status_code or error_code else ""
        super().__init__(f"{prefix} {message}{code_info}")


class NoAvailableProviderError(ProviderError):
    """Ngoại lệ được ném ra khi tất cả các provider trong chuỗi fallback đều thất bại."""
    pass


class ProviderAuthenticationError(ProviderError):
    """Lỗi xác thực với provider (e.g., sai API key, token hết hạn). HTTP 401, 403"""
    pass


class ProviderRateLimitError(ProviderError):
    """Lỗi do vượt quá giới hạn tần suất hoặc hết quota (hết tiền, giới hạn tokens). HTTP 429"""
    pass


class ProviderUnavailableError(ProviderError):
    """Lỗi khi provider không khả dụng hoặc bị timeout đột xuất. HTTP 502, 503, 504"""
    pass


class ResponseValidationError(ProviderError):
    """Lỗi khi phản hồi từ provider không hợp lệ (e.g., sai schema JSON, rỗng)."""
    pass

import httpx
 
def wrap_provider_exception(error: Exception, provider_name: str) -> ProviderError:
    """
    Chuyển đổi các ngoại lệ từ httpx (HTTPStatusError, RequestError) 
    thành Custom Provider Exceptions có đầy đủ cấu trúc mã lỗi.
    """
    # Trường hợp 1: Nếu lỗi đã là Custom Exception của hệ thống, giữ nguyên
    if isinstance(error, ProviderError):
        return error

    # Trường hợp 2: Lỗi HTTPStatusError (Có phản hồi từ API nhưng mã lỗi 4xx, 5xx)
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        error_code = None
        message = str(error)
        raw_response = None
        
        try:
            raw_response = error.response.json()
            # Parser thông minh theo chuẩn chung của các API lớn (OpenAI, Gemini, Anthropic)
            if isinstance(raw_response, dict):
                if "error" in raw_response: # Chuẩn OpenAI, Anthropic
                    error_data = raw_response["error"]
                    if isinstance(error_data, dict):
                        message = error_data.get("message", message)
                        error_code = error_data.get("code")  # e.g., 'insufficient_quota', 'too_many_requests'
                elif "detail" in raw_response: # Chuẩn FastAPI / Một số local provider
                    message = raw_response["detail"]
        except Exception:
            # Fallback nếu response không phải JSON (trả về HTML hoặc text thô)
            message = error.response.text[:500] # Giới hạn kí tự tránh làm phình log

        if status_code in (401, 403):
            return ProviderAuthenticationError(
                message=f"Auth Failed: {message}", provider_name=provider_name,
                status_code=status_code, error_code=error_code, raw_response=raw_response
            )
        elif status_code == 429:
            return ProviderRateLimitError(
                message=f"Quota/Rate Limit Exceeded: {message}", provider_name=provider_name,
                status_code=status_code, error_code=error_code, raw_response=raw_response
            )
        elif status_code in (502, 503, 504):
            return ProviderUnavailableError(
                message=f"Provider Service Unavailable: {message}", provider_name=provider_name,
                status_code=status_code, error_code=error_code, raw_response=raw_response
            )
        else:
            return ProviderError(
                message=message, provider_name=provider_name,
                status_code=status_code, error_code=error_code, raw_response=raw_response
            )

    # Trường hợp 3: Lỗi httpx.RequestError (Mất mạng, Timeout, DNS sập, Không có response)
    if isinstance(error, httpx.RequestError):
        # Tự động map lỗi timeout kết nối vật lý vào nhóm Tạm thời không khả dụng (để sau này RetryPolicy biết đường thử lại)
        if isinstance(error, httpx.TimeoutException):
            return ProviderUnavailableError(
                message=f"Network Timeout: {str(error)}",
                provider_name=provider_name,
                is_network_error=True
            )
        
        return ProviderError(
            message=f"Network Request Failed (No Response): {str(error)}",
            provider_name=provider_name,
            is_network_error=True
        )

    # Trường hợp 4: Các lỗi ngoại vi khác (Lỗi code logic, lỗi hệ thống)
    return ProviderError(message=str(error), provider_name=provider_name)