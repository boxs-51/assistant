from abc import ABC, abstractmethod
from typing import Dict, Tuple

class AuthStrategy(ABC):
    """Interface cho các chiến lược xác thực khác nhau."""
    @abstractmethod
    def prepare_request(self, url: str, headers: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        """
        Chuẩn bị URL và headers cho việc xác thực.
        Trả về một tuple (url_mới, headers_mới).
        """
        pass

class BearerToken(AuthStrategy):
    """Xác thực bằng Bearer Token trong header Authorization."""
    def __init__(self, access_token: str):
        self.access_token = access_token

    def prepare_request(self, url: str, headers: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        headers["Authorization"] = f"Bearer {self.access_token}"
        return url, headers
    
class ApiKeyHeader(AuthStrategy):
    def __init__(self, api_key: str, header_name="x-goog-api-key"):
        self.api_key = api_key
        self.header_name = header_name

    def prepare_request(self, url, headers):
        headers[self.header_name] = self.api_key
        return url, headers

class ApiKeyInQuery(AuthStrategy):
    """Xác thực bằng cách thêm API key vào query parameter của URL."""
    def __init__(self, api_key: str, key_name: str = "key"):
        self.api_key = api_key
        self.key_name = key_name

    def prepare_request(self, url: str, headers: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        # Thêm key vào URL
        separator = '&' if '?' in url else '?'
        new_url = f"{url}{separator}{self.key_name}={self.api_key}"
        return new_url, headers

class NoAuth(AuthStrategy):
    """Chiến lược không yêu cầu xác thực, dùng cho các provider local."""
    def prepare_request(self, url: str, headers: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        # Không làm gì cả, chỉ trả về URL và headers gốc
        return url, headers