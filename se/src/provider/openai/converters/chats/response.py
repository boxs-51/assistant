import httpx
import json

from typing import AsyncGenerator

from ....exceptions import ResponseValidationError
from .....domain.schemas import (
    GatewayResponse,
    GatewayStreamChunk,
    ResponseMetaData,
    
)
class ResponseChats():

    async def adapt_chat(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ OpenAI về GatewayResponse."""
        try:
            response_data = response.json()
            if not isinstance(response_data, dict):
                raise TypeError("response body must be a JSON object")
            # Pydantic model sẽ tự động validate cấu trúc
            response_data["metadata"] = {
                "provider": "openai",
                "provider_response_id": response_data.get("id"),
                "raw_response": response_data.copy(),
            }
            return GatewayResponse.model_validate(response_data)
        except ResponseValidationError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise ResponseValidationError(f"Invalid response structure from OpenAI-compatible API: {e}", provider_name="openai") from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream của OpenAI (SSE) sang stream các GatewayStreamChunk."""
        async for line in response.aiter_lines():
            line = line.decode('utf-8').strip() if isinstance(line, bytes) else line.strip()
            if line.startswith("data: "):
                data = line[len("data: "):]
                if data == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(data)
                    if not isinstance(chunk_json, dict):
                        raise TypeError("stream chunk must be a JSON object")
                    chunk_json["metadata"] = {"provider": "openai", "provider_response_id": chunk_json.get("id")}
                    yield GatewayStreamChunk.model_validate(chunk_json)
                except ResponseValidationError:
                    raise
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    raise ResponseValidationError(
                        f"Invalid stream chunk from OpenAI-compatible API: {e}",
                        provider_name="openai",
                    ) from e
