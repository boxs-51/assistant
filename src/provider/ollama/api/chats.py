from typing import AsyncGenerator, Dict, Any

from ...core.interfaces.chat import ChatProvider
from ...core import ApiType, BaseProvider
from ....domain.schemas import GatewayResponse, GatewayStreamChunk

from ..converters.chat.request import RequestChats
from ..converters.chat.response import ResponseChats


class OllamaChats(ChatProvider):
    def __init__(self, provider: BaseProvider):
        self.request = RequestChats()
        self.response = ResponseChats()
        self.provider = provider

    def prepare_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Chuẩn bị body cho request: dịch tên model và adapt body.
        """
        prepared = body.copy()
        # Dịch tên model, sử dụng default_model nếu có, hoặc lấy từ body, hoặc 'default'
        model = body.get("model")

        translated_model = self.provider.mapper.translate(model)
        prepared["model"] = translated_model

        return self.request.adapt_chat_request(request_chat=prepared)
    
    async def chat(self, **kwargs) -> GatewayResponse:
        prepared_body = self.prepare_request(kwargs.get("body"))
        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout")
        )
        return await self.response.adapt_chat(response=response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body = self.prepare_request(kwargs.get("body"))
        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout")
        )
        
        async for chunk in self.response.adapt_chat_stream(response=response):
            yield chunk
