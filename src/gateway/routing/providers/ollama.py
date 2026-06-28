import httpx
import json
from typing import Dict, Any, AsyncGenerator

from .base import BaseProvider
from ...config import settings
from ...schemas import GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage, GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
from ..exceptions import ResponseValidationError

class OllamaProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình local qua Ollama."""
    def __init__(self):
        super().__init__(
            name="ollama",
            api_url=f"{settings.OLLAMA_BASE_URL}/api/chat",
            headers={"Content-Type": "application/json"}
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Ollama base URL đã được cung cấp hay chưa."""
        return bool(settings.OLLAMA_BASE_URL)

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        # Ollama không dùng Bearer token
        # Chúng ta không raise_for_status() ở đây nữa, vì logic xử lý lỗi nằm ở Executor
        response = await http_client.post(self.api_url, json=body, headers=self.headers, timeout=timeout)
        return response

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        """Adapter: Chuyển đổi response từ định dạng Ollama sang GatewayResponse (chuẩn OpenAI)."""
        try:
            response.raise_for_status() # Kiểm tra lỗi HTTP ở đây
            ollama_json = response.json()

            message_data = ollama_json.get("message", {})
            
            return GatewayResponse(
                model=ollama_json.get("model", model),
                choices=[
                    GatewayChoice(
                        index=0,
                        message=GatewayMessage(
                            role=message_data.get("role", "assistant"),
                            content=message_data.get("content", "")
                        ),
                        finish_reason="stop" if ollama_json.get("done") else None,
                    )
                ],
                usage=GatewayUsage(
                    prompt_tokens=ollama_json.get("prompt_eval_count", 0),
                    completion_tokens=ollama_json.get("eval_count", 0),
                    total_tokens=ollama_json.get("prompt_eval_count", 0) + ollama_json.get("eval_count", 0)
                ),
                raw_response=response
            )
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ResponseValidationError(f"Invalid response structure from Ollama: {str(e)}", provider_name=self.name) from e

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Adapter: Chuyển đổi stream của Ollama sang stream các GatewayStreamChunk."""
        async for line in response.aiter_lines():
            if not line:
                continue
            try:
                ollama_chunk = json.loads(line)
                message_chunk = ollama_chunk.get("message", {})
                
                yield GatewayStreamChunk(
                    model=ollama_chunk.get("model", model),
                    choices=[GatewayStreamChoice(
                        index=0,
                        delta=GatewayStreamDelta(content=message_chunk.get("content", "")),
                        finish_reason="stop" if ollama_chunk.get("done") else None
                    )]
                )
            except json.JSONDecodeError:
                # Bỏ qua các dòng không phải JSON
                continue