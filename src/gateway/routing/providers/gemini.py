import httpx
import structlog
from typing import Dict, Any, AsyncGenerator
from .base import BaseProvider
from ...config import settings
from ..exceptions import ResponseValidationError
from ...schemas import GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage, GatewayStreamChunk

logger = structlog.get_logger(__name__)

class GeminiProvider(BaseProvider):
    """Nhà cung cấp cho Gemini API, hoạt động như một Adapter."""
    def __init__(self):
        super().__init__(
            name="gemini",
            api_url=f"{settings.GEMINI_BASE_URL}/v1beta/models/gemini-pro:generateContent",
            headers={"Content-Type": "application/json"}
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(settings.GEMINI_API_KEY)

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

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        adapted_body = self._adapt_request_body(body)
        request_url = f"{self.api_url}?key={settings.GEMINI_API_KEY}"
        
        # Gemini không trả về response chuẩn OpenAI, nên chúng ta không thể trả về response gốc.
        # Thay vào đó, chúng ta sẽ xử lý nó trong normalize_response.
        return await http_client.post(request_url, json=adapted_body, headers=self.headers, timeout=timeout)

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        """Adapter: Chuyển đổi response từ định dạng Gemini về GatewayResponse."""
        response.raise_for_status()
        gemini_json = response.json()
        try:
            content = gemini_json["candidates"][0]["content"]["parts"][0]["text"]
            return GatewayResponse(
                model=model,
                choices=[GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content=content),
                    finish_reason="stop" # Gemini v1beta không có finish_reason rõ ràng
                )],
                usage=GatewayUsage(), # Gemini v1beta không trả về usage
                raw_response=response
            )
        except (KeyError, IndexError) as e:
            logger.error("Failed to adapt Gemini response", error=str(e), gemini_response=gemini_json)
            raise ResponseValidationError("Invalid response structure from Gemini", provider_name=self.name) from e

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        # TODO: Implement Gemini stream normalization
        # Đây là một ví dụ placeholder, cần logic thực tế để parse stream của Gemini
        raise NotImplementedError("Gemini streaming normalization is not yet implemented.")
        yield
        