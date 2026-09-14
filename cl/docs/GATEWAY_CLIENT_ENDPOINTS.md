# Gateway client endpoints

`GatewayLLMClient` in `cl/src/core/gateway_client.py` uses the gateway base URL
and sends the bearer token supplied to its constructor.

```python
from core.gateway_client import GatewayLLMClient

client = GatewayLLMClient("http://127.0.0.1:8000", api_key="<token>")
models = client.list_models("mock")
embeddings = client.embeddings({"model": "mock-embedding", "input": ["hello"]})
```

| Client method | HTTP endpoint |
| --- | --- |
| `health`, `readiness`, `metrics`, `stats` | `GET /health`, `/ready`, `/metrics`, `/stats` |
| `send_request` | `POST /v1/chat/completions` |
| `embeddings` | `POST /v1/embeddings` |
| `register`, `verify_registration`, `login`, `refresh_token`, `logout` | `POST /v1/auth/...` |
| `current_user`, `list_api_keys` | `GET /v1/auth/...` |
| `create_api_key`, `revoke_api_key` | `POST`/`DELETE /v1/auth/api-keys...` |
| `oauth_redirect_url`, `oauth_login` | `GET`/`POST /v1/auth/oauth/...` |
| `list_models`, `model_details` | `GET /v1/models/...` |
| `list_files`, `upload_file`, `file_metadata`, `download_file`, `delete_file` | `/v1/files/...` |
| `register_agent`, `register_tool` | `POST /v1/agents/`, `/v1/tools/` |
| `create_agent_session`, `add_agent_to_session`, `list_agent_messages` | `/v1/multi-agent/sessions...` |
| `send_agent_message`, `create_agent_task`, `get_agent_task` | `/v1/multi-agent/messages` and `/tasks...` |
| `cancel_agent_task`, `execute_agent_task`, `close_agent_session`, `get_agent_execution` | `/v1/multi-agent/...` |
| `reload_routing`, `circuit_breakers_status` | `/v1/admin/...` |

`/v1/events/ws` is a WebSocket endpoint and is not handled by the synchronous
`requests` client. Use a WebSocket client and send JSON messages with
`action: "subscribe"` or `action: "unsubscribe"` plus `event_name`.