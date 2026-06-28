import httpx
import json
from typing import Dict, Any, AsyncGenerator

from .base import BaseProvider
from ...config import settings
from ...schemas import (
    GatewayResponse, GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
)
from ..exceptions import ResponseValidationError

class OpenAIProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình của OpenAI hoặc các API tương thích OpenAI."""
    def __init__(self):
        super().__init__(
            name="openai",
            api_url=f"{settings.OPENAI_BASE_URL}/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}"
            }
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem OpenAI API key đã được cung cấp hay chưa."""
        return bool(settings.OPENAI_API_KEY)

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        """Thực hiện request đến OpenAI API."""
        return await http_client.post(self.api_url, json=body, headers=self.headers, timeout=timeout)

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        """Adapter: Chuyển đổi response từ định dạng OpenAI JSON sang GatewayResponse."""
        try:
            response.raise_for_status()
            openai_json = response.json()
            # Vì response của OpenAI đã là chuẩn, chúng ta chỉ cần parse và validate nó bằng Pydantic model
            return GatewayResponse.model_validate({**openai_json, "raw_response": response})
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ResponseValidationError(f"Invalid response structure from OpenAI: {str(e)}", provider_name=self.name) from e

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Adapter: Chuyển đổi stream của OpenAI sang stream các GatewayStreamChunk."""
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                line_data = line[len("data: "):]
                if line_data.strip() == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(line_data)
                    # Tương tự như non-streaming, chỉ cần parse và validate
                    yield GatewayStreamChunk.model_validate(chunk_json)
                except json.JSONDecodeError:
                    continue