import httpx
from typing import Dict, Any

class ResponseEmbeddings():
    async def adapt_embeddings_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Chuyển đổi response cho embeddings. Mặc định là trả về JSON gốc."""
        return await response.json()