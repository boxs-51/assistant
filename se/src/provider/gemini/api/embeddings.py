from typing import Dict, Any

from ...core.provider import BaseProvider, ApiType
from ...core.interfaces.embedding import EmbeddingProvider
from ..converters.embeddings.request import RequestEmbeddings 
from ..converters.embeddings.response import ResponseEmbeddings 

class GeminiEmbeddings(EmbeddingProvider):

    def __init__(self, provider : BaseProvider):
        self.request = RequestEmbeddings()
        self.response = ResponseEmbeddings()
        self.provider = provider


    async def embeddings(self, **kwargs) -> Dict[str, Any]:
        """Tạo embeddings cho văn bản bằng API của Gemini."""
        body = kwargs.get("body")
        body = kwargs.get("body") or {}
        # Keep the caller-selected Gemini embedding model.  Default to the
        # currently supported Gemini embedding model used by the live suite.
        embedding_model = str(body.get("model") or "gemini-embedding-001").replace("models/", "")
        # Adapt request body
        adapted_body = self.request.adapt_embeddings_request({"model": embedding_model, **body})

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
        return await self.response.adapt_embeddings_response(
            response=response,
            model=embedding_model,
        )