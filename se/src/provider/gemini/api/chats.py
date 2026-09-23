from typing import Any, AsyncGenerator, Dict

from ...core.interfaces.chat import ChatProvider
from ...core import ApiType, BaseProvider
from ...core.tool_contract import ProviderToolNameMap
from ...core.tool_request_context import build_request_tool_name_map
from ....domain.schemas import GatewayResponse, GatewayStreamChunk

from ..converters.chats.request import RequestChats
from ..converters.chats.response import ResponseChats


class GeminiChat(ChatProvider):
    def __init__(self, provider: BaseProvider):
        self.request = RequestChats()
        self.response = ResponseChats()
        self.provider = provider

    def _prepare_request_with_names(
        self,
        body: Dict[str, Any],
    ) -> tuple[Dict[str, Any], ProviderToolNameMap]:
        names = build_request_tool_name_map("gemini", body)
        return (
            self.request.adapt_chat(
                request=body,
                tool_names=names,
            ),
            names,
        )

    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body")
        client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        model = body.get("model")
        translated_model = self.provider.mapper.translate(model)
        prepared_body, names = self._prepare_request_with_names(body)

        response = await self.provider.send(
            client=client,
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=timeout,
            model=translated_model,
            action="generateContent",
        )
        return await self.response.adapt_chat(
            response,
            tool_names=names,
        )

    async def chat_stream(
        self,
        **kwargs,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body")
        timeout = kwargs.get("timeout")
        client = kwargs.get("http_client")

        model = body.get("model")
        translated_model = self.provider.mapper.translate(model)
        prepared_body, names = self._prepare_request_with_names(body)

        async with self.provider.send_stream(
            client=client,
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=timeout,
            model=translated_model,
            action="streamGenerateContent",
        ) as response:
            async for chunk in self.response.adapt_chat_stream(
                response=response,
                tool_names=names,
            ):
                yield chunk
