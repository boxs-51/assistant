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
                    provider_name="ollama", model_name="gemma3:4b"
                )
            except Exception as e:
                self.render_block(role="system", text=f"❌ Lỗi: {str(e)}")
            finally:
                self.render_block(role="assistant", btype="stream_end")
                self._eval_js("window.setInputState(true)")
                if acquired: lock.release()

        threading.Thread(target=_worker, daemon=True).start()

    # --- Uỷ quyền API cho frontend JS gọi ---
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
    