import base64
import json
import uuid
import logging
import threading
import time
import webview
from .workspace import WorkspaceManager
from .session import SessionManager
from .hitl import HitlManager
from .encoder import FileEncoder
from ..schemas.message import GatewayMessage
from ..schemas.request import GatewayChatRequest, RequestConfig

logger = logging.getLogger(__name__)

MAX_CANONICAL_IN_MEMORY_CONTENT_BYTES = 32 * 1024 * 1024

class UIBridge:
    def __init__(self, engine, hitl, client_runtime):
        self._engine = engine
        self._client_runtime = client_runtime
        self._window = None
        self._execution_local = threading.local()
        self._state_lock = threading.RLock()
        self._activity = []
        self._chat_preferences = {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "execution_mode": "ONLINE_AGENT",
            "agent_enabled": True,
            "agent_id": "agent-coordinator",
        }
        
        # Gắn kết các module
        raw_ws = getattr(engine, "workspace_dir", __import__("os").getcwd())
        self.workspace = WorkspaceManager(raw_ws)
        self.sessions = SessionManager()
        
        timeout = getattr(engine, "approval_timeout_seconds", 300.0)
        self.hitl = HitlManager(self._eval_js, self._execution_local, timeout)
        self.encoder = FileEncoder(self._eval_js)
        
        hitl.set_approval_callback(self.hitl.show_dialog)

    def login(self, payload: dict):
        try:
            return {
                "success": True,
                "data": self._client_runtime.login(payload),
            }
        except Exception as error:
            logger.exception("Login failed")
            return {
                "success": False,
                "error": str(error),
            }

    def _auth_action(self, action, payload: dict):
        try:
            return {"success": True, "data": action(payload)}
        except Exception as error:
            logger.exception("Authentication action failed")
            return {"success": False, "error": str(error)}

    def register(self, payload: dict):
        return self._auth_action(self._client_runtime.register, payload)

    def verify_registration(self, payload: dict):
        return self._auth_action(self._client_runtime.verify_registration, payload)

    def initiate_password_reset(self, payload: dict):
        return self._auth_action(self._client_runtime.initiate_password_reset, payload)

    def confirm_password_reset(self, payload: dict):
        return self._auth_action(self._client_runtime.confirm_password_reset, payload)

    def logout(self):
        return self._auth_action(lambda _payload: self._client_runtime.logout(), {})

    def _record_activity(self, action: str, status: str, detail=None):
        item = {
            "id": uuid.uuid4().hex,
            "action": action,
            "status": status,
            "detail": detail,
            "created_at": time.time(),
        }
        with self._state_lock:
            self._activity.insert(0, item)
            del self._activity[100:]
        return item

    @staticmethod
    def _public_skill(skill: dict) -> dict:
        return {
            key: value for key, value in skill.items()
            if key not in {"path", "content", "mtime_ns"}
        }

    def get_app_snapshot(self):
        registry = self._engine.registry
        with self._state_lock:
            activity = list(self._activity)
            preferences = dict(self._chat_preferences)

        startup_error = None
        if not self._client_runtime.ready:
            try:
                self._client_runtime.start()
            except Exception as error:
                logger.exception("Gateway session initialization failed")
                startup_error = str(error)

        capabilities = []
        agents = []
        gateway_status = {
            "connected": self._client_runtime.ready,
            "principal_type": getattr(self._client_runtime, "principal_type", None),
            "user_id": getattr(self._client_runtime, "owner_id", None),
        }
        if startup_error:
            gateway_status["error"] = startup_error
        if self._client_runtime.ready:
            try:
                gateway_status["health"] = self._engine.gateway_client.health()
                capabilities = self._engine.gateway_client.list_capabilities()
                agents = self._engine.gateway_client.list_agents()
            except Exception as error:
                gateway_status["error"] = str(error)

        local_tools = []
        for name, item in registry.tools.items():
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            local_tools.append({
                "name": name,
                "description": metadata.get("description", name),
                "parameters": metadata.get("parameters", metadata.get("input_schema", {"type": "object"})),
                "base_risk": metadata.get("base_risk", "MEDIUM"),
                "source": "MCP" if item.get("is_mcp") else "LOCAL",
            })

        return {
            "gateway": gateway_status,
            "preferences": preferences,
            "skills": [self._public_skill(item) for item in registry.skills.values()],
            "tools": local_tools,
            "capabilities": capabilities,
            "support_catalog": {
                kind.lower() + "s": [
                    item for item in capabilities if item.get("kind") == kind
                ]
                for kind in ("TOOL", "SKILL", "AGENT")
            },
            "agents": agents,
            "activity": activity,
        }

    def set_chat_preferences(self, payload: dict):
        provider = str(payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        if not provider or not model:
            return {"success": False, "error": "Provider và model không được để trống."}
        execution_mode = str(
            payload.get("execution_mode")
            or self._chat_preferences.get("execution_mode")
            or "ONLINE_AGENT"
        ).upper()
        if execution_mode not in {"ONLINE_AGENT", "LOCAL_OFFLINE"}:
            return {"success": False, "error": "execution_mode khong hop le."}
        agent_enabled = payload.get(
            "agent_enabled", self._chat_preferences.get("agent_enabled", True)
        )
        if not isinstance(agent_enabled, bool):
            return {"success": False, "error": "agent_enabled phai la boolean."}
        agent_id = str(
            payload.get("agent_id")
            if "agent_id" in payload
            else self._chat_preferences.get("agent_id") or ""
        ).strip() or None
        with self._state_lock:
            self._chat_preferences = {
                "provider": provider,
                "model": model,
                "execution_mode": execution_mode,
                "agent_enabled": agent_enabled,
                "agent_id": agent_id,
            }
        return {"success": True, "data": dict(self._chat_preferences)}

    def list_models(self, provider: str):
        try:
            return {"success": True, "data": self._engine.gateway_client.list_models(provider)}
        except Exception as error:
            return {"success": False, "error": str(error)}

    def activate_skill(self, skill_name: str):
        try:
            if not self._client_runtime.ready:
                raise RuntimeError("Vui lòng đăng nhập trước khi kích hoạt Skill.")
            skill = self._engine.registry.activate_skill(skill_name)
            registered = self._engine.gateway_client.register_skill({
                "name": skill["name"],
                "description": skill.get("description", skill["name"]),
                "version": skill.get("version", "1.0"),
                "instruction": skill["content"],
                "metadata": {"base_risk": skill.get("base_risk", "MEDIUM")},
            })
            self._record_activity("skill.activate", "success", skill_name)
            return {
                "success": True,
                "data": {"skill": self._public_skill(skill), "registration": registered},
            }
        except Exception as error:
            self._record_activity("skill.activate", "error", str(error))
            return {"success": False, "error": str(error)}

    def deactivate_skill(self, skill_name: str):
        unloaded = self._engine.registry.deactivate_skill(skill_name)
        self._record_activity("skill.deactivate", "success" if unloaded else "error", skill_name)
        return {"success": unloaded, "data": {"name": skill_name, "loaded": False}}

    def execute_tool(self, tool_name: str = None, tool_arguments: dict = None):
        if not isinstance(tool_name, str) or not tool_name.strip():
            return {"success": False, "error": "Tool name is required."}
        tool_name = tool_name.strip()
        tool_arguments = tool_arguments or {}
        if not isinstance(tool_arguments, dict):
            return {"success": False, "error": "Tool arguments phải là object."}
        try:
            local = self._engine.registry.get_tool(tool_name)
            metadata = local.get("metadata", {}) if isinstance(local, dict) else {}
            risk_level = str(metadata.get("base_risk", "HIGH")).upper()
            approved = self._engine.hitl.request_approval(
                tool_name,
                tool_arguments,
                risk_level,
                "Thực thi Tool trực tiếp từ giao diện.",
            )
            if not approved:
                raise PermissionError("Tool execution was not approved.")
            result = self._engine.gateway_client.execute_capability(
                tool_name,
                {"arguments": tool_arguments},
            )
            self._record_activity("tool.execute", "success", tool_name)
            return {"success": True, "data": result}
        except Exception as error:
            self._record_activity("tool.execute", "error", str(error))
            return {"success": False, "error": str(error)}

    def save_agent(self, payload: dict):
        try:
            result = self._engine.gateway_client.register_capability_agent(payload)
            self._record_activity("agent.save", "success", payload.get("name"))
            return {"success": True, "data": result}
        except Exception as error:
            self._record_activity("agent.save", "error", str(error))
            return {"success": False, "error": str(error)}

    def run_agent(self, payload: dict):
        try:
            agent_id = str(payload.get("agent_id") or "").strip()
            prompt = str(payload.get("prompt") or "").strip()
            if not agent_id or not prompt:
                raise ValueError("Agent và prompt không được để trống.")
            session = self._engine.gateway_client.create_agent_session([agent_id])
            with self._state_lock:
                model = self._chat_preferences["model"]
            task_payload = {
                "session_id": session["session_id"],
                "assigned_agent_id": agent_id,
                "input": {"prompt": prompt, "model": model},
            }
            connection_id = getattr(self._client_runtime, "connection_id", None)
            if connection_id:
                task_payload["connection_id"] = connection_id
            task = self._engine.gateway_client.create_agent_task(task_payload)
            started = self._engine.gateway_client.start_agent_task(task["task_id"])
            self._record_activity("agent.run", "running", agent_id)
            return {"success": True, "data": {"session": session, "task": started}}
        except Exception as error:
            self._record_activity("agent.run", "error", str(error))
            return {"success": False, "error": str(error)}

    def get_agent_task_status(self, task_id: str):
        try:
            task = self._engine.gateway_client.get_agent_task(task_id)
            return {"success": True, "data": task}
        except Exception as error:
            return {"success": False, "error": str(error)}

    def cancel_agent_task(self, task_id: str):
        try:
            task = self._engine.gateway_client.cancel_agent_task(task_id)
            self._record_activity("agent.cancel", "success", task_id)
            return {"success": True, "data": task}
        except Exception as error:
            self._record_activity("agent.cancel", "error", str(error))
            return {"success": False, "error": str(error)}
    
    def set_window(self, window: webview.Window):
        self._window = window

    def _eval_js(self, js_code: str):
        if self._window:
            try: self._window.evaluate_js(js_code)
            except Exception as e: logger.warning("Lỗi JS: %s", e)

    def render_block(self, role="assistant", btype=None, data=None, **kwargs):
        payload = {"role": role, "data": data, **kwargs}
        if btype: payload["type"] = btype
        self._eval_js(f"window.renderBlock && window.renderBlock({json.dumps(payload, ensure_ascii=False)})")

    @staticmethod
    def _canonical_attachment_from_descriptor(descriptor: dict) -> dict:
        asset_id = str(descriptor.get("asset_id") or "").strip()
        if not asset_id:
            raise ValueError("Canonical asset upload did not return asset_id.")
        if str(descriptor.get("state") or "").upper() != "READY":
            raise ValueError("Canonical asset upload did not reach READY state.")
        return {
            "asset_id": asset_id,
            "source": "asset",
            "uri": f"asset://{asset_id}",
            "filename": descriptor.get("filename"),
            "mime_type": descriptor.get("mime_type") or "application/octet-stream",
            "size": descriptor.get("size_bytes"),
        }

    @classmethod
    def _online_message_content(cls, text: str, files: list | None):
        attachments = files or []
        if not attachments:
            return text

        parts = []
        if text:
            parts.append({"type": "text", "text": text})

        for item in attachments:
            if not isinstance(item, dict):
                raise ValueError("ONLINE attachment payload must be canonical asset metadata.")
            asset_id = str(item.get("asset_id") or "").strip()
            if not asset_id or item.get("source") != "asset":
                raise ValueError(
                    "ONLINE attachments must be READY canonical assets; re-attach failed or legacy files."
                )
            if any(item.get(field) not in (None, "") for field in (
                "base64_data", "b64_data", "bytes_data", "provider_file_id", "path"
            )):
                raise ValueError(
                    "ONLINE canonical attachments cannot carry local/base64/provider identity."
                )

            attachment = {
                "asset_id": asset_id,
                "source": "asset",
                "uri": f"asset://{asset_id}",
                "filename": item.get("filename"),
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "size": item.get("size"),
            }
            mime_type = attachment["mime_type"]
            if mime_type.startswith("image/"):
                parts.append({
                    "type": "image",
                    "data": {"attachment": attachment, "detail": "auto"},
                })
            elif mime_type.startswith("audio/"):
                parts.append({"type": "audio", "data": {"attachment": attachment}})
            elif mime_type.startswith("video/"):
                parts.append({"type": "video", "data": {"attachment": attachment}})
            else:
                parts.append({"type": "file", "data": {"attachment": attachment}})
        return parts

    def _upload_asset_worker(self, file_path: str) -> None:
        progress = {"path": file_path, "progress": 5}
        self._eval_js(
            "window.onFilePrepareProgress && window.onFilePrepareProgress("
            + json.dumps(progress, ensure_ascii=False)
            + ")"
        )
        try:
            descriptor = self._engine.gateway_client.upload_asset(file_path)
            attachment = self._canonical_attachment_from_descriptor(descriptor)
            payload = {"path": file_path, "payload": attachment}
            self._eval_js(
                "window.onFilePrepareComplete && window.onFilePrepareComplete("
                + json.dumps(payload, ensure_ascii=False)
                + ")"
            )
        except Exception as error:
            logger.exception("Canonical asset upload failed: %s", file_path)
            payload = {"path": file_path, "error": str(error)}
            self._eval_js(
                "window.onFilePrepareError && window.onFilePrepareError("
                + json.dumps(payload, ensure_ascii=False)
                + ")"
            )

    def prepare_files_async(self, files: list):
        if not files:
            return None
        with self._state_lock:
            execution_mode = self._chat_preferences.get("execution_mode")
        if execution_mode == "LOCAL_OFFLINE":
            return self.encoder.encode_async(files)
        if not self._client_runtime.ready:
            for file_path in files:
                payload = {
                    "path": file_path,
                    "error": "Gateway authentication is not ready.",
                }
                self._eval_js(
                    "window.onFilePrepareError && window.onFilePrepareError("
                    + json.dumps(payload, ensure_ascii=False)
                    + ")"
                )
            return None
        for file_path in files:
            threading.Thread(
                target=self._upload_asset_worker,
                args=(file_path,),
                daemon=True,
            ).start()
        return None

    def read_asset_content(
        self,
        asset_id: str,
        byte_range: str = None,
        max_bytes: int = None,
    ):
        try:
            metadata = self._engine.gateway_client.asset_metadata(asset_id)

            if max_bytes is not None:
                try:
                    bounded_max_bytes = int(max_bytes)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "Canonical asset content bound must be an integer."
                    ) from error
                if bounded_max_bytes <= 0:
                    raise ValueError(
                        "Canonical asset content bound must be positive."
                    )
                bounded_max_bytes = min(
                    bounded_max_bytes,
                    MAX_CANONICAL_IN_MEMORY_CONTENT_BYTES,
                )

                raw_size = metadata.get("size_bytes")
                try:
                    asset_size = int(raw_size)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "Canonical asset size metadata is unavailable or invalid."
                    ) from error
                if asset_size < 0:
                    raise ValueError(
                        "Canonical asset size metadata must be non-negative."
                    )
                if asset_size > bounded_max_bytes:
                    raise ValueError(
                        "Canonical asset exceeds the bounded in-memory content "
                        f"limit ({asset_size} > {bounded_max_bytes} bytes)."
                    )

            content = self._engine.gateway_client.asset_content(
                asset_id,
                byte_range=byte_range,
            )
            return {
                "success": True,
                "data": {
                    "asset_id": asset_id,
                    "filename": metadata.get("filename"),
                    "mime_type": (
                        metadata.get("mime_type")
                        or "application/octet-stream"
                    ),
                    "base64_data": base64.b64encode(content).decode("ascii"),
                },
            }
        except Exception as error:
            logger.exception("Canonical asset content read failed: %s", asset_id)
            return {"success": False, "error": str(error)}

    def submit_prompt(self, text: str, files: list = None, conversation_id: str = None):

        if not self._client_runtime.ready:
            self.render_block(
                role="system",
                text="Vui lòng đăng nhập trước khi sử dụng Gateway.",
            )
            return
        
        cid, session, lock = self.sessions.get_or_create(conversation_id)
        execution_id = uuid.uuid4().hex
        self._eval_js("window.setInputState(false)")

        def _worker():
            acquired = False
            try:
                lock.acquire()
                acquired = True
                self._execution_local.execution_id = execution_id
                
                if text and text.startswith("/"):
                    res = self._engine.registry.execute_slash_command(text)
                    return self.render_block(role="system", text=str(res))

                logger.info("thong tin cua file", files=files)
                self.render_block(role="user", data={"text": text, "files": files or []})
                self._eval_js("window.showPendingIndicator()")
                
                with self._state_lock:
                    preferences = dict(self._chat_preferences)
                if preferences.get("execution_mode") == "LOCAL_OFFLINE":
                    self._engine.run_agent_session(
                        session=session, user_input=text, attached_files=files or [],
                        render_cb=self.render_block, enable_stream=True,
                        provider_name=preferences["provider"], model_name=preferences["model"]
                    )
                else:
                    message_content = self._online_message_content(text, files)
                    response = self._client_runtime.chat(
                        GatewayChatRequest(
                            model=preferences["model"],
                            messages=[GatewayMessage(role="user", content=message_content)],
                            session_id=cid,
                            agent_enabled=bool(preferences.get("agent_enabled")),
                            agent_id=(
                                preferences.get("agent_id")
                                if preferences.get("agent_enabled")
                                else None
                            ),
                            config=RequestConfig(
                                stream=True,
                                agent_activity_stream=bool(preferences.get("agent_enabled")),
                            ),
                        )
                    )
                    if isinstance(response, dict):
                        self.render_block(
                            role="system",
                            btype="execution_status",
                            data=response,
                        )
                    else:
                        for chunk in response:
                            if isinstance(chunk, dict):
                                block_type = (
                                    "agent_event"
                                    if chunk.get("object") == "agent_stream_event"
                                    else "execution_status"
                                )
                                self.render_block(
                                    role="system",
                                    btype=block_type,
                                    data=chunk,
                                )
                            else:
                                self.render_block(
                                    role="assistant",
                                    btype="stream_content",
                                    data=chunk.model_dump(mode="json"),
                                )
            except Exception as e:
                self.render_block(role="system", text=f"❌ Lỗi: {str(e)}")
            finally:
                self.render_block(role="assistant", btype="stream_end")
                self._eval_js("window.setInputState(true)")
                if acquired: lock.release()

        threading.Thread(target=_worker, daemon=True).start()

    def execute_gateway_endpoint(self, endpoint: str, payload: dict = None):
        """Execute a user-selected, allowlisted Gateway client operation."""
        payload = payload or {}
        client = self._engine.gateway_client

        operations = {
            "health": lambda: client.health(),
            "readiness": lambda: client.readiness(),
            "stats": lambda: client.stats(),
            "metrics": lambda: client.metrics(),
            "login": lambda: client.login(payload),
            "register": lambda: client.register(payload),
            "verify_registration": lambda: client.verify_registration(payload),
            "refresh_token": lambda: client.refresh_token(payload["refresh_token"]),
            "logout": lambda: client.logout(payload["refresh_token"]),
            "oauth_login": lambda: client.oauth_login(payload["provider"], payload),
            "current_user": lambda: client.current_user(),
            "list_api_keys": lambda: client.list_api_keys(),
            "create_api_key": lambda: client.create_api_key(payload),
            "revoke_api_key": lambda: client.revoke_api_key(payload["key_id"]),
            "embeddings": lambda: client.embeddings(payload),
            "list_models": lambda: client.list_models(payload["provider_name"]),
            "model_details": lambda: client.model_details(payload["provider_name"], payload["model_id"]),
            "register_agent": lambda: client.register_agent(payload),
            "register_tool": lambda: client.register_tool(payload),
            "register_capability_tool": lambda: client.register_capability_tool(payload),
            "register_skill": lambda: client.register_skill(payload),
            "register_capability_agent": lambda: client.register_capability_agent(payload),
            "get_sessions": lambda: client.get_sessions(),
            "get_session": lambda: client.get_session(payload["session_id"]),
            "edit_session_message": lambda: client.edit_session_message(payload["session_id"], payload["message_id"], payload["content"]),
            "regenerate_session_response": lambda: client.regenerate_session_response(payload["session_id"], payload),
            "create_agent_session": lambda: client.create_agent_session(payload["agent_ids"]),
            "add_agent_to_session": lambda: client.add_agent_to_session(payload["session_id"], payload["agent_id"]),
            "list_agent_messages": lambda: client.list_agent_messages(payload["session_id"]),
            "send_agent_message": lambda: client.send_agent_message(payload),
            "create_agent_task": lambda: client.create_agent_task(payload),
            "get_agent_task": lambda: client.get_agent_task(payload["task_id"]),
            "cancel_agent_task": lambda: client.cancel_agent_task(payload["task_id"]),
            "execute_agent_task": lambda: client.execute_agent_task(payload["task_id"]),
            "close_agent_session": lambda: client.close_agent_session(payload["session_id"]),
            "get_agent_execution": lambda: client.get_agent_execution(payload["execution_id"]),
            "reload_routing": lambda: client.reload_routing(),
            "circuit_breakers_status": lambda: client.circuit_breakers_status(),
        }
        if endpoint not in operations:
            return {"success": False, "error": f"Endpoint không được phép: {endpoint}"}

        try:
            return {"success": True, "endpoint": endpoint, "data": operations[endpoint]()}
        except Exception as error:
            logger.exception("Gateway endpoint failed: %s", endpoint)
            return {"success": False, "endpoint": endpoint, "error": str(error)}

    # --- Uỷ quyền API cho frontend JS gọi ---
    def get_sessions(self):
        # Sidebar initialization races with the Gateway panel during pywebview
        # startup, so session bootstrap must not depend on panel ordering.
        self._client_runtime.start()
        return self._engine.gateway_client.get_sessions()
    def encode_files_async(self, files: list): return self.encoder.encode_async(files)
    def respond_approval(self, choice: bool, aid: str = None): return self.hitl.respond(choice, aid)
    def get_workspace_files(self): return self.workspace.get_files()
    def read_file_content(self, path: str): return self.workspace.read_file(path)
    def save_file_content(self, path: str, content: str): return self.workspace.save_file(path, content)
    def rename_file_content(self, old_path:str, new_name:str): return self.workspace.rename_item(old_path, new_name)
    def delete_file_content(self, path: str): return self.workspace.delete_item(path)
    def open_file_picker(self) -> list:
        if not self._window:
            return []
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
            )
            return list(result) if result else []
        except Exception as e:
            logger.error("Lỗi mở File Picker: %s", e)
            return []
