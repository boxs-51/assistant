from ...core.interfaces.chat import ChatProvider
from ...core import ApiType, BaseProvider
from ....domain.schemas import GatewayResponse, GatewayStreamChunk
from typing import AsyncGenerator, Dict, Any

from ..converters.chats.request import RequestChats 
from ..converters.chats.response import ResponseChats 

import structlog
logger = structlog.get_logger(__name__)

class GeminiChat(ChatProvider):
    def __init__(self, provider: BaseProvider):
        self.request = RequestChats()
        self.response = ResponseChats()
        self.provider = provider

    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body")
        client=kwargs.get("http_client")
        timeout=kwargs.get("timeout")

        model = body.get("model")

        translated_model = self.provider.mapper.translate(model)
        prepared_body = self.request.adapt_chat(request=body)

        action = "generateContent"

        response = await self.provider.send(
            client=client,
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=timeout,
            model=translated_model,
            action=action
        )
        return await self.response.adapt_chat(response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body")
        timeout=kwargs.get("timeout")
        client=kwargs.get("http_client")

        model = body.get("model")

        translated_model = self.provider.mapper.translate(model)
        prepared_body = self.request.adapt_chat(request=body)

        action = "streamGenerateContent"

        async with self.provider.send_stream(
            client=client,
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=timeout,
            model=translated_model,
            action=action
        )as response:
            async for chunk in self.response.adapt_chat_stream(response=response):
                yield chunk