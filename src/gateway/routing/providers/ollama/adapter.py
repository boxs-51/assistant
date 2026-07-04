import json
from typing import Dict, Any, AsyncGenerator

from ....schemas import (
    GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage,
    GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
)
from ..base.adapter import BaseAdapter
from ...exceptions import ResponseValidationError
import httpx
class OllamaAdapter(BaseAdapter):
    """Adapter để chuyển đổi định dạng request/response của Ollama."""

    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Chuyển đổi request body từ chuẩn Gateway sang chuẩn Ollama.
        Trong trường hợp này, API /api/chat của Ollama khá tương thích.
        """
        # Chúng ta chỉ cần đảm bảo model được truyền đúng cách
        adapted_request = request.copy()
        # Không cần thay đổi nhiều, nhưng có thể thêm logic để loại bỏ các trường không được hỗ trợ
        return adapted_request

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse: # Make it async
        """Chuyển đổi response JSON từ Ollama về GatewayResponse."""
        try:
            response_data = await response.json()
            message_data = response_data.get("message", {})
            
            return GatewayResponse(
                model=response_data.get("model", "default"),
                choices=[
                    GatewayChoice(
                        index=0,
                        message=GatewayMessage(
                            role=message_data.get("role", "assistant"),
                            content=message_data.get("content", "")
                        ),
                        finish_reason="stop" if response_data.get("done") else None,
                    )
                ],
                usage=GatewayUsage(
                    prompt_tokens=response_data.get("prompt_eval_count", 0),
                    completion_tokens=response_data.get("eval_count", 0),
                    total_tokens=response_data.get("prompt_eval_count", 0) + response_data.get("eval_count", 0)
                )
            )
        except (KeyError, IndexError) as e:
            raise ResponseValidationError(f"Invalid response structure from Ollama: {str(e)}", provider_name="ollama") from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream của Ollama (JSON objects trên mỗi dòng) sang stream các GatewayStreamChunk."""
        async for line in response.aiter_lines():
            if not line:
                continue
            try:
                ollama_chunk = json.loads(line)
                message_chunk = ollama_chunk.get("message", {})
                
                yield GatewayStreamChunk(
                    model=ollama_chunk.get("model", "default"),
                    choices=[GatewayStreamChoice(
                        index=0,
                        delta=GatewayStreamDelta(content=message_chunk.get("content", "")),
                        finish_reason="stop" if ollama_chunk.get("done") else None
                    )]
                )
            except json.JSONDecodeError:
                continue