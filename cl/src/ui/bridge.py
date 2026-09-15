import json
import uuid
import logging
import threading
import webview
from .workspace import WorkspaceManager
from .session import SessionManager
from .hitl import HitlManager
from .encoder import FileEncoder

logger = logging.getLogger(__name__)

class UIBridge:
    def __init__(self, engine, hitl):
        self._engine = engine
        self._window = None
        self._execution_local = threading.local()
        
        # Gắn kết các module
        raw_ws = getattr(engine, "workspace_dir", __import__("os").getcwd())
        self.workspace = WorkspaceManager(raw_ws)
        self.sessions = SessionManager()
        
        timeout = getattr(engine, "approval_timeout_seconds", 300.0)
        self.hitl = HitlManager(self._eval_js, self._execution_local, timeout)
        self.encoder = FileEncoder(self._eval_js)
        
        hitl.set_approval_callback(self.hitl.show_dialog)

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

    def submit_prompt(self, text: str, files: list = None, conversation_id: str = None):
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
                
                self._engine.run_agent_session(
                    session=session, user_input=text, attached_files=files or [],
                    render_cb=self.render_block, enable_stream=True,
                    provider_name="gemini", model_name="gemini-2.5-flash"
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
    def get_sessions(self): return self._engine.gateway_client.get_sessions()
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
