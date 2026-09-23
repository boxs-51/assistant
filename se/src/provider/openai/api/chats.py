from typing import Any, AsyncGenerator, Dict

from ...core.interfaces.chat import ChatProvider
from ...core import BaseProvider, ApiType
from ...core.tool_contract import ProviderToolNameMap
from ...core.tool_request_context import build_request_tool_name_map
from ....domain.schemas import GatewayResponse, GatewayStreamChunk

from ..converters.chats.request import RequestChats
from ..converters.chats.response import ResponseChats


class OpenAIChats(ChatProvider):
    def __init__(self, provider: BaseProvider):
        self.request = RequestChats()
        self.response = ResponseChats()
        self.provider = provider

    def _prepare_request_with_names(
        self,
        body: Dict[str, Any],
    ) -> tuple[Dict[str, Any], ProviderToolNameMap]:
        prepared = body.copy()
        model = body.get("model")
        prepared["model"] = self.provider.mapper.translate(model)

        names = build_request_tool_name_map("openai", prepared)
        return (
            self.request.adapt_chat_request(
                request=prepared,
                tool_names=names,
            ),
            names,
        )

    def prepare_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare an OpenAI request while retaining public dict compatibility."""

        prepared, _ = self._prepare_request_with_names(body)
        return prepared

    async def chat(self, **kwargs) -> GatewayResponse:
        prepared_body, names = self._prepare_request_with_names(kwargs.get("body"))
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
        prepared_body, names = self._prepare_request_with_names(kwargs.get("body"))
        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
        )

        async for chunk in self.response.adapt_chat_stream(
            response=response,
            tool_names=names,
        ):
            yield chunk
