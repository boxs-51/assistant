import httpx
from typing import Dict, Any
from .base import BaseProvider
from ...config import settings

class OpenAIProvider(BaseProvider):
    """Nhà cung cấp cho OpenAI API."""
    def __init__(self):
        super().__init__(
            name="openai",
            api_url=f"{settings.OPENAI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
        )

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        return await http_client.post(self.api_url, json=body, headers=self.headers, timeout=timeout)