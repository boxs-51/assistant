from typing import Any, AsyncGenerator, Dict

from ...core.interfaces.chat import ChatProvider
from ...core import ApiType, BaseProvider
from ...core.tool_contract import ProviderToolNameMap
from ...core.tool_request_context import build_request_tool_name_map
from ....domain.schemas import GatewayResponse, GatewayStreamChunk

from ..converters.chat.request import RequestChats
from ..converters.chat.response import ResponseChats


class OllamaChats(ChatProvider):
    def __init__(self, provider: BaseProvider):
        self.request = RequestChats()
        self.response = ResponseChats()
        self.provider = provider

    def _prepare_request_with_names(
        self,
        body: Dict[str, Any],
        *,
        stream: bool,
    ) -> tuple[Dict[str, Any], ProviderToolNameMap]:
        prepared = body.copy()
        prepared["model"] = self.provider.mapper.translate(body.get("model"))
        names = build_request_tool_name_map("ollama", prepared)
        return (
            self.request.adapt_chat_request(
                request=prepared,
                stream=stream,
                tool_names=names,
            ),
            names,
        )

    def prepare_request(
        self,
        body: Dict[str, Any],
        stream: bool = False,
    ) -> Dict[str, Any]:
        prepared, _ = self._prepare_request_with_names(body, stream=stream)
        return prepared

    async def chat(self, **kwargs) -> GatewayResponse:
        prepared_body, names = self._prepare_request_with_names(
            kwargs.get("body"),
            stream=False,
        )
        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
        )
        return await self.response.adapt_chat(
            response=response,
            tool_names=names,
        )

    async def chat_stream(
        self,
        **kwargs,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body, names = self._prepare_request_with_names(
            kwargs.get("body"),
            stream=True,
        )

        async with self.provider.send_stream(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
        ) as response:
            async for chunk in self.response.adapt_chat_stream(
                response=response,
                tool_names=names,
            ):
                yield chunk
