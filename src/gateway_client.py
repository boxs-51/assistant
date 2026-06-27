import httpx
from typing import Optional

class AIGatewayClient:
    """
    Client để giao tiếp với AI Gateway.
    Lớp này đóng gói việc gọi API đến endpoint của gateway.
    """
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "my-secret-client-key"):
        self.base_url = base_url
        # API Key này sẽ được dùng để xác thực với Gateway
        self.api_key = api_key
        self.http_client = httpx.AsyncClient(timeout=120.0)

    async def generate(self, system_prompt: str, prompt: str, model: str = "gpt-4o") -> str:
        """
        Gửi yêu cầu generate đến AI Gateway, tuân thủ schema của OpenAI.
        """
        endpoint = f"{self.base_url}/v1/chat/completions"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        body = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        response = await self.http_client.post(endpoint, json=body, headers=headers)
        response.raise_for_status() # Ném lỗi nếu status code là 4xx hoặc 5xx
        
        response_json = response.json()
        return response_json["choices"][0]["message"]["content"]