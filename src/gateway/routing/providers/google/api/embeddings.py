from typing import Dict, Any

from ...core.provider import BaseProvider, ApiType
from ...core.interfaces.embedding import EmbeddingProvider
from ..converters.embeddings.request import RequestEmbeddings 
from ..converters.embeddings.response import ResponseEmbeddings 

class GoogleEmbeddings(EmbeddingProvider):

    def __init__(self, provider : BaseProvider):
        self.request = RequestEmbeddings()
        self.response = ResponseEmbeddings()
        self.provider = provider


    async def embeddings(self, **kwargs) -> Dict[str, Any]:
        """Tạo embeddings cho văn bản bằng API của Gemini."""
        body = kwargs.get("body")
        # Gemini sử dụng model embedding riêng, không giống model chat
        embedding_model = "embedding-001"
        # Adapt request body
        adapted_body = self.request.adapt_embeddings_request(request_embeddings={"model": embedding_model, **body})

        action = "embedContent"
        # Nếu là batch request, action sẽ khác
        if "requests" in adapted_body:
            action = "batchEmbedContents"

        response = await self.provider.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.EMBEDDINGS,
            json=adapted_body,
            timeout=kwargs.get("timeout"),
            model=embedding_model,
            action=action
        )
        return await self.response.adapt_embeddings_response(response=response)