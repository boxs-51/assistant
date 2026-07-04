import json
from typing import Dict, Any, AsyncGenerator

import httpx
from ....schemas import (
    GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage,
    GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
)
from ..base.adapter import BaseAdapter
from ...exceptions import ResponseValidationError

class GeminiAdapter(BaseAdapter):
    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi body từ định dạng OpenAI sang định dạng Gemini."""
        gemini_contents = []
        system_prompt = ""
        user_messages = []

        for msg in request.get("messages", []):
            if msg.get("role") == "system":
                system_prompt += msg.get("content", "") + "\n"
            else:
                user_messages.append(msg)

        if system_prompt and user_messages:
            user_messages[0]['content'] = system_prompt + user_messages[0]['content']

        for message in user_messages:
            role = "user" if message.get("role") != "assistant" else "model"
            gemini_contents.append({"role": role, "parts": [{"text": message.get("content")}]})

        gemini_body = {"contents": gemini_contents}
        if "temperature" in request:
            gemini_body["generationConfig"] = {"temperature": request["temperature"]}
        
        return gemini_body

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ Gemini về GatewayResponse."""
        try:
            response_data = await response.json()
            response_data = response.json()
            candidate = response_data["candidates"][0]
            content = candidate["content"]["parts"][0]["text"]
            finish_reason = candidate.get("finishReason", "stop")
            model = response_data.get("model", "default")
            return GatewayResponse(
                model=model,
                choices=[GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content=content),
                    finish_reason=finish_reason
                )],
                usage=GatewayUsage() # Gemini v1beta không trả về usage
            )
        except (KeyError, IndexError) as e:
            raise ResponseValidationError(f"Invalid response structure from Gemini: {str(e)}", provider_name="gemini") from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuẩn hóa stream của Gemini (Server-Sent Events format)."""
        async for byte_chunk in response.aiter_bytes():
            # Gemini stream có thể chứa nhiều event trong một chunk, cần xử lý
            lines = byte_chunk.decode('utf-8').splitlines()
            for line in lines:
                line = line.strip()
                if line.startswith("data:"):
                    content = line[len("data:"):].strip()
                    if not content or content == "[DONE]":
                        continue
                    try:
                        chunk_json = json.loads(content)
                        text_delta = chunk_json["candidates"][0]["content"]["parts"][0]["text"]
                        yield GatewayStreamChunk(
                            model=chunk_json.get("model", "default"),
                            choices=[GatewayStreamChoice(
                                index=0,
                                delta=GatewayStreamDelta(content=text_delta)
                            )]
                        )
                    except (json.JSONDecodeError, KeyError, IndexError):
                        # Bỏ qua các chunk không hợp lệ hoặc không chứa content
                        continue