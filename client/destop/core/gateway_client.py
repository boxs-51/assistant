import requests
import json
from typing import Generator, Union
from src.gateway.schemas.request import GatewayChatRequest
from src.gateway.schemas.response import GatewayResponse, GatewayStreamChunk

class GatewayLLMClient:
    def __init__(self, gateway_url: str, api_key: str = ""):
        self.gateway_url = gateway_url.rstrip('/') + "/v1/chat/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def send_request(self, payload: GatewayChatRequest) -> Union[GatewayResponse, Generator[GatewayStreamChunk, None, None]]:
        # Chuyển Pydantic Model thành JSON Dict
        json_data = payload.model_dump(exclude_none=True)

        if payload.config.stream:
            return self._stream_response(json_data)
        else:
            response = requests.post(self.gateway_url, headers=self.headers, json=json_data)
            response.raise_for_status()
            return GatewayResponse.model_validate(response.json())

    def _stream_response(self, json_data: dict) -> Generator[GatewayStreamChunk, None, None]:
        with requests.post(self.gateway_url, headers=self.headers, json=json_data, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith("data: "):
                        data_str = line_str[6:].strip()
                        if data_str == "[DONE]":
                            break
                        chunk_dict = json.loads(data_str)
                        yield GatewayStreamChunk.model_validate(chunk_dict)