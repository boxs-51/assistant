from ...base.interfaces.chat import ChatProvider
from ...base.api import ApiType
from .....schemas import GatewayResponse, GatewayStreamChunk
from typing import AsyncGenerator

from ..converters.chat.request import ChatRequest
from ..converters.chat.response import ChatResponse

class ChatGemini():

    def __init__(self):
        self.request = ChatRequest()
        self.reponse = ChatResponse()

    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body")
        
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model") # Lấy model đã được dịch

        action = "generateContent"

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
            model=provider_model,
            action=action
        )
        return await self.reponse.adapt_chat_response(response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body")
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model")

        action = "streamGenerateContent"

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
            model=provider_model,
            action=action
        )
        async for chunk in self.reponse.adapt_chat_stream(response):
            yield chunk