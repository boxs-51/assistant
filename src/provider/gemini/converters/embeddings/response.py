import httpx
from typing import Dict, Any, List

class ResponseEmbeddings():
    async def adapt_embeddings_response(
        self,
        response: httpx.Response,
        model: str,
    ) -> Dict[str, Any]:
        """Normalize Gemini embedding responses to the Gateway/OpenAI shape."""
        raw = response.json()

        vectors: List[List[float]] = []
        if isinstance(raw.get("embedding"), dict):
            vectors.append(list(raw["embedding"].get("values") or []))

        for item in raw.get("embeddings", []) or []:
            if isinstance(item, dict):
                vectors.append(list(item.get("values") or []))

        return {
            "object": "list",
            "model": model,
            "data": [
                {
                    "object": "embedding",
                    "index": index,
                    "embedding": vector,
                }
                for index, vector in enumerate(vectors)
            ],
            "usage": {
                "prompt_tokens": raw.get("usageMetadata", {}).get("promptTokenCount", 0),
                "total_tokens": raw.get("usageMetadata", {}).get("totalTokenCount", 0),
            },
        }