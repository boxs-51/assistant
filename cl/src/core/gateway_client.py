import requests
import json
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional, Union
from ..schemas.request import GatewayChatRequest
from ..schemas.response import GatewayResponse, GatewayStreamChunk

# Token cố định cho Guest (phải trùng khớp với GUEST_PASS_TOKEN bên backend JWTAuthenticator)
DEFAULT_GUEST_TOKEN = "YOUR_GUEST_PASS_JWT_HERE"

class GatewayLLMClient:
    def __init__(self, gateway_url: str, api_key: str = ""):
        self.base_url = gateway_url.rstrip('/')
        self.gateway_url = self.base_url + "/v1/chat/completions"
        token = api_key or DEFAULT_GUEST_TOKEN
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = self.headers.copy()
        if "files" in kwargs:
            headers.pop("Content-Type", None)
        response = requests.request(
            method,
            f"{self.base_url}/{path.lstrip('/')}",
            headers=headers,
            **kwargs,
        )
        response.raise_for_status()
        return response

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

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()

    def readiness(self) -> Dict[str, Any]:
        return self._request("GET", "/ready").json()

    def stats(self) -> Dict[str, Any]:
        return self._request("GET", "/stats").json()

    def metrics(self) -> str:
        return self._request("GET", "/metrics").text

    def register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/auth/register/initiate", json=payload).json()

    def verify_registration(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/auth/register/verify", json=payload).json()

    def login(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/auth/login", json=payload).json()

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return self._request("POST", "/v1/auth/refresh", json={"refresh_token": refresh_token}).json()

    def logout(self, refresh_token: str) -> None:
        self._request("POST", "/v1/auth/logout", json={"refresh_token": refresh_token})

    def oauth_redirect_url(self, provider: str) -> str:
        return f"{self.base_url}/v1/auth/oauth/login/{provider}"

    def oauth_login(self, provider: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/v1/auth/oauth/{provider}", json=payload).json()

    def current_user(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/auth/me").json()

    def create_api_key(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/auth/api-keys", json=payload).json()

    def list_api_keys(self) -> Any:
        return self._request("GET", "/v1/auth/api-keys").json()

    def revoke_api_key(self, key_id: str) -> None:
        self._request("DELETE", f"/v1/auth/api-keys/{key_id}")

    def embeddings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/embeddings", json=payload).json()

    def list_models(self, provider_name: str) -> Any:
        return self._request("GET", "/v1/models/", params={"provider_name": provider_name}).json()

    def model_details(self, provider_name: str, model_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/models/{model_id}",
            params={"provider_name": provider_name},
        ).json()

    def list_files(
        self,
        provider_name: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> Any:
        params = {"provider_name": provider_name}
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token
        return self._request("GET", "/v1/files/", params=params).json()

    def upload_file(
        self,
        provider_name: str,
        file_path: Union[str, Path],
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = Path(file_path)
        with path.open("rb") as file_handle:
            files = {"file": (path.name, file_handle, "application/octet-stream")}
            params = {"provider_name": provider_name}
            if display_name is not None:
                params["display_name"] = display_name
            return self._request("POST", "/v1/files/", params=params, files=files).json()

    def file_metadata(self, provider_name: str, file_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/files/{file_id}",
            params={"provider_name": provider_name, "action": "metadata"},
        ).json()

    def download_file(self, provider_name: str, file_id: str) -> bytes:
        return self._request(
            "GET",
            f"/v1/files/{file_id}",
            params={"provider_name": provider_name, "action": "download"},
        ).content

    def delete_file(self, provider_name: str, file_id: str) -> None:
        self._request("DELETE", f"/v1/files/{file_id}", params={"provider_name": provider_name})

    def register_agent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/agents/", json=payload).json()

    def register_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/tools/", json=payload).json()

    def create_agent_session(self, agent_ids: Iterable[str]) -> Dict[str, Any]:
        return self._request("POST", "/v1/multi-agent/sessions", json={"agent_ids": list(agent_ids)}).json()

    def add_agent_to_session(self, session_id: str, agent_id: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/multi-agent/sessions/{session_id}/agents",
            json={"agent_id": agent_id},
        ).json()

    def list_agent_messages(self, session_id: str) -> Any:
        return self._request("GET", f"/v1/multi-agent/sessions/{session_id}/messages").json()

    def send_agent_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/multi-agent/messages", json=payload).json()

    def create_agent_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/multi-agent/tasks", json=payload).json()

    def get_agent_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/multi-agent/tasks/{task_id}").json()

    def cancel_agent_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/multi-agent/tasks/{task_id}/cancel").json()

    def execute_agent_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/multi-agent/tasks/{task_id}/execute").json()

    def close_agent_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/multi-agent/sessions/{session_id}/close").json()

    def get_agent_execution(self, execution_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/multi-agent/executions/{execution_id}").json()

    def reload_routing(self) -> Dict[str, Any]:
        return self._request("POST", "/v1/admin/reload/routing").json()

    def circuit_breakers_status(self) -> Any:
        return self._request("GET", "/v1/admin/circuit-breakers/status").json()