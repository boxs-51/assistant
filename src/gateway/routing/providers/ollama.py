import httpx
from typing import Dict, Any
from .base import BaseProvider
from ...config import settings

class OllamaProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình local qua Ollama."""
    def __init__(self):
        super().__init__(
            name="ollama",
            api_url=f"{settings.OLLAMA_BASE_URL}/api/chat",
            headers={"Content-Type": "application/json"}
        )

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        # Ollama không dùng Bearer token
        return await http_client.post(self.api_url, json=body, headers=self.headers, timeout=timeout)