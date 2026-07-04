import json
from typing import Dict, Any, AsyncGenerator

from ....schemas import GatewayResponse, GatewayStreamChunk
from ..base.adapter import BaseAdapter
from ...exceptions import ResponseValidationError
import httpx
class OpenAIAdapter(BaseAdapter):
    """
    Adapter cho các API tương thích với OpenAI.
    Vì schema nội bộ của gateway dựa trên OpenAI, adapter này chủ yếu
    thực hiện việc xác thực và chuyển đổi kiểu dữ liệu.
    """
    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Request đã ở định dạng chuẩn OpenAI, không cần thay đổi."""
        return request

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ OpenAI về GatewayResponse."""
        response_data = await response.json()
        try:
            # Pydantic model sẽ tự động validate cấu trúc
            return GatewayResponse.model_validate(response_data)
        except Exception as e:
            # Bắt các lỗi validation từ Pydantic
            raise ResponseValidationError(f"Invalid response structure from OpenAI-compatible API: {e}", provider_name="openai") from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream của OpenAI (SSE) sang stream các GatewayStreamChunk."""
        async for line in response.aiter_lines():
            line = line.decode('utf-8').strip()
            if line.startswith("data: "):
                data = line[len("data: "):]
                if data == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(data)
                    yield GatewayStreamChunk.model_validate(chunk_json)
                except (json.JSONDecodeError, Exception) as e:
                    # Bỏ qua các dòng không hợp lệ hoặc lỗi parse
                    continue