from ...base.interfaces.chat import ChatProvider
from ...base import ApiType, BaseProvider
from .....schemas import GatewayResponse, GatewayStreamChunk
from typing import AsyncGenerator, Dict, Any

from ..converters.chats.request import RequestChats 
from ..converters.chats.response import ResponseChats 

import structlog
logger = structlog.get_logger(__name__)

class GoogleChat(ChatProvider):
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

        return self.request.adapt_chat(request=prepared)
    
    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body")
        client=kwargs.get("http_client")
        timeout=kwargs.get("timeout")
        
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model") # Lấy model đã được dịch

        action = "generateContent"

        response = await self.provider.send(
            client=client,
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=timeout,
            model=provider_model,
            action=action
        )
        return await self.response.adapt_chat(response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body")
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model")

        action = "streamGenerateContent"

        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
            model=provider_model,
            action=action
        )
        async for chunk in self.response.adapt_chat_stream(response=response):
            yield chunk