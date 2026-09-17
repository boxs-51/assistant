import requests
import json
import uuid
import websocket
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional, Union
from ..schemas.request import GatewayChatRequest
from ..schemas.response import GatewayResponse, GatewayStreamChunk
from .realtime_client import GatewayRealtimeClient as PersistentGatewayRealtimeClient

# Token cố định cho Guest (phải trùng khớp với GUEST_PASS_TOKEN bên backend JWTAuthenticator)
DEFAULT_GUEST_TOKEN = "YOUR_GUEST_PASS_JWT_HERE"

class GatewayLLMClient:
    def __init__(self, gateway_url: str, api_key: str = ""):
        self.base_url = gateway_url.rstrip('/')
        self.gateway_url = self.base_url + "/v1/chat/completions"

        self.access_token = api_key or DEFAULT_GUEST_TOKEN
        self.refresh_token_value = None

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token
        self.headers["Authorization"] = f"Bearer {access_token}"

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
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = response.text
            raise requests.HTTPError(
                f"{response.status_code}: {detail}",
                response=response,
            )
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
        response = self._request(
            "POST",
            "/v1/auth/login",
            json=payload,
        ).json()

        self.set_access_token(response["access_token"])
        self.refresh_token_value = response.get("refresh_token")

        return response

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        ).json()

        self.set_access_token(response["access_token"])
        return response

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

    def list_agents(self) -> Any:
        return self._request("GET", "/v1/agents/").json()

    def register_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/tools/", json=payload).json()

    def list_tools(self) -> Any:
        return self._request("GET", "/v1/tools/").json()

    def register_capability_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/capabilities/tools", json=payload).json()

    def register_skill(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/capabilities/skills", json=payload).json()

    def register_capability_agent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/capabilities/agents", json=payload).json()

    def list_capabilities(self, kind: Optional[str] = None) -> Any:
        params = {"kind": kind} if kind else None
        return self._request("GET", "/v1/capabilities/", params=params).json()

    def execute_capability(self, capability_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/capabilities/{capability_id}/execute",
            json=payload,
        ).json()

    def get_sessions(self) -> Any:
        return self._request("GET", "/v1/sessions").json()

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{session_id}").json()

    def edit_session_message(self, session_id: str, message_id: str, content: Any) -> Dict[str, Any]:
        return self._request("PATCH", f"/v1/sessions/{session_id}/messages/{message_id}", json={"content": content}).json()

    def regenerate_session_response(self, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{session_id}/regenerate", json=payload).json()

    def sync_registry(self, registry, agent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = {"tools": [], "skills": [], "agent": None}
        for name, item in registry.tools.items():
            metadata = item.get("metadata", {})
            result["tools"].append(self.register_capability_tool({"name": name, "description": metadata.get("description", name), "parameters": metadata.get("parameters", {"type": "object"})}))
        for name in registry.skills:
            skill = registry.get_skill(name, load=True)
            result["skills"].append(self.register_skill({
                "name": name,
                "description": skill.get("description", name),
                "version": skill.get("version", "1.0"),
                "instruction": skill["content"],
                "metadata": {"base_risk": skill.get("base_risk", "MEDIUM")},
            }))
        if agent:
            result["agent"] = self.register_capability_agent(agent)
        return result

    def open_realtime_connection(self, connection_id: Optional[str] = None, session_id: Optional[str] = None):
        return PersistentGatewayRealtimeClient(self.base_url, self.headers, connection_id=connection_id, session_id=session_id)

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

    def start_agent_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/multi-agent/tasks/{task_id}/start").json()

    def close_agent_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/multi-agent/sessions/{session_id}/close").json()

    def get_agent_execution(self, execution_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/multi-agent/executions/{execution_id}").json()

    def reload_routing(self) -> Dict[str, Any]:
        return self._request("POST", "/v1/admin/reload/routing").json()

    def circuit_breakers_status(self) -> Any:
        return self._request("GET", "/v1/admin/circuit-breakers/status").json()


class GatewayRealtimeClient:
    """
    DEPRECATED.

    Use cl.src.core.realtime_client.GatewayRealtimeClient.
    """
    def __init__(self, base_url: str, headers: Dict[str, str], connection_id: Optional[str] = None, session_id: Optional[str] = None):
        self.connection_id = connection_id or f"cl-{uuid.uuid4().hex}"
        self.session_id = session_id or f"session-{uuid.uuid4().hex}"
        self._headers = headers
        self._ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/v1/events/ws"
        self.ws = None

    def connect(self) -> Dict[str, Any]:
        self.ws = websocket.create_connection(self._ws_url, header=[f"Authorization: {self._headers['Authorization']}"])
        self.send("connection.register", {"client_id": "desktop-client"})
        return self.receive()

    def send(self, event_type: str, payload: Dict[str, Any], invocation_id: Optional[str] = None) -> None:
        if self.ws is None:
            raise RuntimeError("Realtime connection is not open.")
        self.ws.send(json.dumps({"type": event_type, "message_id": uuid.uuid4().hex, "session_id": self.session_id, "connection_id": self.connection_id, "invocation_id": invocation_id, "payload": payload}))

    def receive(self) -> Dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Realtime connection is not open.")
        return json.loads(self.ws.recv())

    def send_result(self, invocation_id: str, result: Any) -> None:
        self.send("capability.result", {"result": result}, invocation_id)

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
