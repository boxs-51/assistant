import httpx
import time
import uuid
import structlog
from typing import Dict, Any
from .base import BaseProvider
from ...config import settings
from ..exceptions import ResponseValidationError

logger = structlog.get_logger(__name__)

class GeminiProvider(BaseProvider):
    """Nhà cung cấp cho Gemini API, hoạt động như một Adapter."""
    def __init__(self):
        super().__init__(
            name="gemini",
            api_url=f"{settings.GEMINI_BASE_URL}/v1beta/models/gemini-pro:generateContent",
            headers={"Content-Type": "application/json"}
        )

    def _adapt_request_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Adapter: Chuyển đổi body từ định dạng OpenAI sang định dạng Gemini."""
        gemini_contents = []
        # Logic đơn giản: gộp system prompt vào message đầu tiên của user
        system_prompt = ""
        user_messages = []
        for msg in body.get("messages", []):
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
        if "temperature" in body:
            gemini_body["generationConfig"] = {"temperature": body["temperature"]}
        return gemini_body

    def _adapt_response_body(self, gemini_response: Dict[str, Any], model: str) -> Dict[str, Any]:
        """Adapter: Chuyển đổi response từ định dạng Gemini về định dạng OpenAI."""
        try:
            content = gemini_response["candidates"][0]["content"]["parts"][0]["text"]
            openai_response = {
                "id": f"chatcmpl-gemini-{uuid.uuid4()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": { # Gemini API v1beta cho gemini-pro không trả về usage trong body.
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                },
            }
            return openai_response
        except (KeyError, IndexError) as e:
            logger.error("Failed to adapt Gemini response", error=str(e), gemini_response=gemini_response)
            raise ResponseValidationError("Invalid response structure from Gemini", provider_name="gemini") from e

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        adapted_body = self._adapt_request_body(body)
        request_url = f"{self.api_url}?key={settings.GEMINI_API_KEY}"
        
        original_response = await http_client.post(request_url, json=adapted_body, headers=self.headers, timeout=timeout)
        original_response.raise_for_status()
        
        openai_formatted_body = self._adapt_response_body(original_response.json(), body.get("model", "gemini-pro"))
        
        return httpx.Response(
            status_code=200, headers={'content-type': 'application/json'},
            json=openai_formatted_body, request=original_response.request
        )