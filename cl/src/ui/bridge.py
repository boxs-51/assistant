import os
import json
import shutil
import logging
import threading
import mimetypes
import base64
import uuid
import webview

logger = logging.getLogger(__name__)


class UIBridge:
    """
    P0-hardened UI bridge.

    Guarantees:
    - Filesystem operations are constrained to the canonical workspace root.
    - HITL approval requests are correlated by a unique approval_id.
    - Each conversation/session has its own AgentContextSession and execution lock.
    - Existing public methods remain backward compatible where practical.
    """

    DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300.0

    def __init__(self, engine, hitl):
        self._engine = engine
        self._hitl = hitl
        self._window = None

        # -------------------------------------------------------------
        # Workspace security boundary
        # -------------------------------------------------------------
        raw_workspace_dir = getattr(engine, "workspace_dir", os.getcwd())
        self._workspace_root = self._canonical_workspace_root(raw_workspace_dir)
        self._workspace_dir = self._workspace_root

        # -------------------------------------------------------------
        # Conversation/session isolation
        # -------------------------------------------------------------
        self._sessions = {}
        self._session_locks = {}
        self._sessions_lock = threading.RLock()
        self._default_conversation_id = f"bridge:{uuid.uuid4().hex}"

        # -------------------------------------------------------------
        # HITL correlation
        # -------------------------------------------------------------
        self._approval_lock = threading.RLock()
        self._pending_approvals = {}
        self._approval_timeout_seconds = getattr(
            engine,
            "approval_timeout_seconds",
            self.DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        )

        # -------------------------------------------------------------
        # Per-worker execution context for diagnostics/correlation.
        # This is not used as the primary HITL correlation mechanism.
        # -------------------------------------------------------------
        self._execution_local = threading.local()

        # Đăng ký callback HITL
        self._hitl.set_approval_callback(self.show_approval_dialog)

    # -----------------------------------------------------------------
    # WORKSPACE SECURITY
    # -----------------------------------------------------------------
    @staticmethod
    def _canonical_workspace_root(path: str) -> str:
        if not path:
            raise ValueError("Workspace directory không được để trống")

        root = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        if not os.path.isdir(root):
            raise NotADirectoryError(f"Workspace directory không tồn tại: {root}")
        return os.path.normcase(os.path.normpath(root))

    def _canonicalize_workspace_path(
        self,
        path: str,
        *,
        allow_missing_leaf: bool = False,
        reject_workspace_root: bool = False,
    ) -> str:
        """Resolve a path and guarantee that its canonical target stays inside workspace."""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Path không hợp lệ")

        raw_path = os.path.expanduser(path.strip())
        if os.path.isabs(raw_path):
            candidate = raw_path
        else:
            candidate = os.path.join(self._workspace_root, raw_path)

        candidate_abs = os.path.abspath(candidate)
        canonical = os.path.realpath(candidate_abs)
        canonical = os.path.normcase(os.path.normpath(canonical))

        # realpath() also resolves symlinks/junctions on supported platforms.
        try:
            inside = os.path.commonpath([self._workspace_root, canonical]) == self._workspace_root
        except ValueError:
            inside = False

        if not inside:
            raise PermissionError("Path nằm ngoài workspace hoặc vượt qua workspace boundary")

        if reject_workspace_root and canonical == self._workspace_root:
            raise PermissionError("Không cho phép thao tác phá hủy trên workspace root")

        if not allow_missing_leaf and not os.path.exists(candidate_abs):
            raise FileNotFoundError(f"Path không tồn tại: {path}")

        return canonical

    def _canonicalize_existing_file(self, path: str) -> str:
        canonical = self._canonicalize_workspace_path(path)
        if not os.path.isfile(canonical):
            raise IsADirectoryError(f"Không phải file: {path}")
        return canonical

    def _canonicalize_existing_dir(self, path: str) -> str:
        canonical = self._canonicalize_workspace_path(path)
        if not os.path.isdir(canonical):
            raise NotADirectoryError(f"Không phải thư mục: {path}")
        return canonical

    # -----------------------------------------------------------------
    # SESSION ISOLATION
    # -----------------------------------------------------------------
    @staticmethod
    def _normalize_conversation_id(conversation_id: str) -> str:
        if conversation_id is None:
            return ""
        value = str(conversation_id).strip()
        if len(value) > 256:
            raise ValueError("conversation_id quá dài")
        return value

    def _get_or_create_session(self, conversation_id: str):
        conversation_id = self._normalize_conversation_id(conversation_id)
        if not conversation_id:
            conversation_id = self._default_conversation_id

        with self._sessions_lock:
            session = self._sessions.get(conversation_id)
            if session is None:
                from ..schemas.context import AgentContextSession

                session = AgentContextSession()
                self._sessions[conversation_id] = session
                self._session_locks[conversation_id] = threading.Lock()
            return conversation_id, session, self._session_locks[conversation_id]

    # -----------------------------------------------------------------
    # MULTI-THREADED FILE BASE64 ENCODER
    # -----------------------------------------------------------------
    def encode_files_async(self, file_paths: list):
        """Encode each selected file independently and report progress to JS."""
        if not file_paths:
            return

        for path in file_paths:
            threading.Thread(
                target=self._worker_encode_single_file,
                args=(path,),
                daemon=True,
                name="ui-file-encoder",
            ).start()

    def _worker_encode_single_file(self, file_path: str):
        try:
            # Attachments may legitimately come from outside the workspace.
            # They do NOT grant workspace mutation/read authority to the file APIs.
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                raise FileNotFoundError(f"File không tồn tại: {file_path}")

            file_size = os.path.getsize(file_path)
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            chunk_size = 1024 * 1024
            read_bytes = 0
            raw_bytes = bytearray()

            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    raw_bytes.extend(chunk)
                    read_bytes += len(chunk)

                    progress = int((read_bytes / file_size) * 100) if file_size > 0 else 100
                    self._notify_file_progress(file_path, progress)

            b64_data = base64.b64encode(raw_bytes).decode("utf-8")
            filename = os.path.basename(file_path)
            data_uri = f"data:{mime_type};base64,{b64_data}"

            file_payload = {
                "path": file_path,
                "filename": filename,
                "mime_type": mime_type,
                "b64_data": b64_data,
                "data_uri": data_uri,
                "size": file_size,
            }

            self._notify_file_complete(file_path, file_payload)

        except Exception as e:
            logger.error("Lỗi encode base64 file", exc_info=True, extra={"file_path": file_path})
            self._notify_file_error(file_path, str(e))

    def _notify_file_progress(self, path: str, progress: int):
        data = json.dumps({"path": path, "progress": progress}, ensure_ascii=False)
        self._eval_js(f"window.onFileEncodeProgress && window.onFileEncodeProgress({data})")

    def _notify_file_complete(self, path: str, payload: dict):
        data = json.dumps(payload, ensure_ascii=False)
        self._eval_js(f"window.onFileEncodeComplete && window.onFileEncodeComplete({data})")

    def _notify_file_error(self, path: str, error_msg: str):
        data = json.dumps({"path": path, "error": error_msg}, ensure_ascii=False)
        self._eval_js(f"window.onFileEncodeError && window.onFileEncodeError({data})")

    # -----------------------------------------------------------------
    # API GỌI TỪ JS SANG PYTHON
    # -----------------------------------------------------------------
    def submit_prompt(
        self,
        text: str,
        files: list = None,
        conversation_id: str = None,
    ):
        files = files or []
        conversation_id, session, session_lock = self._get_or_create_session(conversation_id)
        execution_id = uuid.uuid4().hex

        self._eval_js("window.setInputState(false)")

        def _worker():
            acquired = False
            try:
                # One mutable session cannot be executed by two turns concurrently.
                session_lock.acquire()
                acquired = True
                self._execution_local.execution_id = execution_id

                # 1. Slash Command -> System Role ('system')
                if text and text.startswith("/"):
                    res = self._engine.registry.execute_slash_command(text)
                    self.render_block(role="system", text=str(res))
                    return

                # 2. Render files + user prompt
                data={}
                if files:
                    data["files"] = files
                if text and text.strip():
                    data["text"] = text

                self.render_block(role="user", data=data)

                # 3. Spinner
                self._eval_js("window.showPendingIndicator()")

                # 4. Agent Engine — session is owned by this conversation.
                self._engine.run_agent_session(
                    session=session,
                    user_input=text,
                    attached_files=files,
                    render_cb=self.render_block,
                    enable_stream=True,
                    provider_name = "ollama", 
                    model_name = "gemma3:4b",
                )

            except Exception as e:
                logger.error(
                    "Lỗi trong quá trình chạy Agent",
                    exc_info=True,
                    extra={
                        "conversation_id": conversation_id,
                        "execution_id": execution_id,
                    },
                )
                self._eval_js("window.removePendingIndicator()")
                self.render_block(role="system", text=f"❌ System Error: {str(e)}")

            finally:
                try:
                    self.render_block(role="assistant", btype="stream_end")
                    self._eval_js("window.setInputState(true)")
                finally:
                    try:
                        del self._execution_local.execution_id
                    except AttributeError:
                        pass
                    if acquired:
                        session_lock.release()

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"agent-execution-{execution_id[:8]}",
        ).start()

    # -----------------------------------------------------------------
    # HITL CORRELATION
    # -----------------------------------------------------------------
    @staticmethod
    def _coerce_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "approve", "approved", "allow"}:
                return True
            if normalized in {"0", "false", "no", "reject", "rejected", "deny", "denied"}:
                return False
        return bool(value)

    def respond_approval(self, choice: bool, approval_id: str = None):
        """
        Resolve đúng một HITL request theo approval_id.

        Compatibility:
        - Nếu không truyền approval_id và chỉ có 1 request pending -> cho phép.
        - Nếu có nhiều request pending -> bắt buộc phải có approval_id.
        """
        selected_id = (
            approval_id.strip()
            if isinstance(approval_id, str)
            else approval_id
        )

        with self._approval_lock:
            if selected_id is None:
                if len(self._pending_approvals) != 1:
                    logger.warning(
                        "Ambiguous HITL response rejected",
                        extra={
                            "pending_count": len(self._pending_approvals)
                        },
                    )
                    return False

                selected_id = next(iter(self._pending_approvals))

            pending = self._pending_approvals.get(selected_id)

            if pending is None:
                logger.warning(
                    "Unknown HITL approval_id rejected",
                    extra={
                        "approval_id": selected_id
                    },
                )
                return False

            pending["response"] = self._coerce_bool(choice)
            pending["event"].set()
            return True

    def show_approval_dialog(self, req_data: dict) -> bool:
        """
        Hiển thị một approval request độc lập.

        Mỗi request có:
            approval_id
            event
            response

        Nhiều request có thể pending đồng thời.
        """
        if not isinstance(req_data, dict):
            req_data = {
                "message": str(req_data)
            }
        else:
            req_data = dict(req_data)

        approval_id = uuid.uuid4().hex

        execution_id = getattr(
            self._execution_local,
            "execution_id",
            None,
        )

        req_data.setdefault(
            "approval_id",
            approval_id,
        )

        if execution_id:
            req_data.setdefault(
                "execution_id",
                execution_id,
            )

        event = threading.Event()

        pending = {
            "event": event,
            "response": None,
            "execution_id": execution_id,
        }

        # -------------------------------------------------------------
        # IMPORTANT:
        # Register BEFORE sending request to JS.
        #
        # This removes the old race:
        #   JS response -> clear(event)
        #
        # -------------------------------------------------------------
        with self._approval_lock:
            self._pending_approvals[approval_id] = pending

        try:
            data_json = json.dumps(
                req_data,
                ensure_ascii=False,
            )

            self._eval_js(
                "window.showApprovalBar && "
                f"window.showApprovalBar({data_json})"
            )

            completed = event.wait(
                timeout=float(
                    self._approval_timeout_seconds
                )
            )

            with self._approval_lock:
                response = (
                    pending.get("response")
                    if completed
                    else None
                )

                self._pending_approvals.pop(
                    approval_id,
                    None,
                )

            if not completed:
                logger.warning(
                    "HITL approval timed out; denying by default",
                    extra={
                        "approval_id": approval_id,
                    },
                )

                response = False

            # Hide CHÍNH CARD này, không hide các approval khác.
            self._eval_js(
                "window.hideApprovalBar && "
                "window.hideApprovalBar(%s)"
                % json.dumps(approval_id)
            )

            return bool(response)

        except Exception:
            with self._approval_lock:
                self._pending_approvals.pop(
                    approval_id,
                    None,
                )

            try:
                self._eval_js(
                    "window.hideApprovalBar && "
                    "window.hideApprovalBar(%s)"
                    % json.dumps(approval_id)
                )
            except Exception:
                pass

            logger.error(
                "HITL approval dialog failed",
                exc_info=True,
            )

            return False

    # -----------------------------------------------------------------
    # FILE PICKER
    # -----------------------------------------------------------------
    def set_window(self, window: webview.Window):
        self._window = window

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

    # -----------------------------------------------------------------
    # WORKSPACE FILE BROWSER
    # -----------------------------------------------------------------
    def get_workspace_files(self) -> list:
        def _scan_dir(path, max_depth=2, current_depth=0):
            if current_depth > max_depth:
                return []

            items = []
            try:
                for entry in sorted(os.listdir(path)):
                    if entry.startswith(".") or entry in {"__pycache__", "node_modules", "venv"}:
                        continue

                    full_path = os.path.join(path, entry)

                    # Do not recurse through symlinks/junction-like links.
                    if os.path.islink(full_path):
                        continue

                    try:
                        canonical = self._canonicalize_workspace_path(full_path)
                    except (PermissionError, FileNotFoundError):
                        continue

                    rel_path = os.path.relpath(canonical, self._workspace_root)
                    if rel_path == ".":
                        rel_path = ""

                    if os.path.isdir(canonical):
                        items.append(
                            {
                                "name": entry,
                                "type": "folder",
                                "path": canonical,
                                "relative_path": rel_path,
                                "children": _scan_dir(canonical, max_depth, current_depth + 1),
                            }
                        )
                    else:
                        items.append(
                            {
                                "name": entry,
                                "type": "file",
                                "path": canonical,
                                "relative_path": rel_path,
                            }
                        )
            except Exception as e:
                logger.error("Lỗi quét thư mục %s: %s", path, e)
            return items

        return _scan_dir(self._workspace_root)

    def read_file_content(self, file_path: str) -> dict:
        try:
            canonical = self._canonicalize_existing_file(file_path)
            with open(canonical, "r", encoding="utf-8") as f:
                content = f.read()
            return {"success": True, "path": canonical, "content": content}
        except Exception as e:
            logger.warning("Workspace read denied/failed: %s", e)
            return {"success": False, "error": str(e)}

    def save_file_content(self, file_path: str, content: str) -> dict:
        try:
            canonical = self._canonicalize_workspace_path(file_path, allow_missing_leaf=True)
            if canonical == self._workspace_root:
                raise PermissionError("Không thể ghi đè workspace root")

            parent = self._canonicalize_existing_dir(os.path.dirname(canonical))
            target = os.path.join(parent, os.path.basename(canonical))
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "message": "Đã lưu file thành công", "path": canonical}
        except Exception as e:
            logger.warning("Workspace write denied/failed: %s", e)
            return {"success": False, "error": str(e)}

    def paste_item(self, action: str, source_path: str, target_path: str = "") -> dict:
        try:
            if action not in {"copy", "cut"}:
                return {"success": False, "error": "Action không hợp lệ"}

            # Strict P0 sandbox: both source and mutation target must stay in workspace.
            source = self._canonicalize_workspace_path(
                source_path,
                reject_workspace_root=True,
            )
            if os.path.isfile(source):
                source_is_file = True
            elif os.path.isdir(source):
                source_is_file = False
            else:
                return {"success": False, "error": "Nguồn không hợp lệ"}

            if not target_path:
                target_dir = self._workspace_root
            elif os.path.isfile(target_path):
                target_dir = os.path.dirname(
                    self._canonicalize_existing_file(target_path)
                )
            else:
                target_dir = self._canonicalize_existing_dir(target_path)

            target_dir = self._canonicalize_existing_dir(target_dir)

            base_name = os.path.basename(source)
            dest_path = os.path.join(target_dir, base_name)
            dest_path = self._canonicalize_workspace_path(dest_path, allow_missing_leaf=True)

            if action == "copy" and os.path.exists(dest_path):
                name, ext = os.path.splitext(base_name)
                counter = 1
                while os.path.exists(dest_path):
                    dest_path = os.path.join(target_dir, f"{name}_copy_{counter}{ext}")
                    dest_path = self._canonicalize_workspace_path(dest_path, allow_missing_leaf=True)
                    counter += 1

            if os.path.realpath(source) == os.path.realpath(dest_path):
                return {"success": True, "dest_path": dest_path}

            if action == "copy":
                if source_is_file:
                    shutil.copy2(source, dest_path)
                else:
                    # Preserve links instead of following them across filesystem boundaries.
                    shutil.copytree(source, dest_path, symlinks=True)
            else:
                shutil.move(source, dest_path)

            return {"success": True, "dest_path": dest_path}

        except Exception as e:
            logger.error("Lỗi khi %s file: %s", action, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def rename_item(self, old_path: str, new_name: str) -> dict:
        try:
            if not isinstance(new_name, str) or not new_name.strip():
                return {"success": False, "error": "Tên mới không hợp lệ"}
            if new_name in {".", ".."} or os.path.basename(new_name) != new_name:
                return {"success": False, "error": "Tên mới chứa path traversal"}

            old_canonical = self._canonicalize_workspace_path(
                old_path,
                reject_workspace_root=True,
            )
            parent_dir = self._canonicalize_existing_dir(os.path.dirname(old_canonical))
            new_path = self._canonicalize_workspace_path(
                os.path.join(parent_dir, new_name),
                allow_missing_leaf=True,
            )

            if os.path.exists(new_path) and old_canonical != new_path:
                return {"success": False, "error": f"Tên '{new_name}' đã tồn tại"}

            os.rename(old_canonical, new_path)
            return {"success": True, "new_path": new_path}

        except Exception as e:
            logger.error("Lỗi khi đổi tên: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def delete_item(self, path: str) -> dict:
        try:
            canonical = self._canonicalize_workspace_path(
                path,
                reject_workspace_root=True,
            )

            if os.path.isdir(canonical):
                shutil.rmtree(canonical)
            else:
                os.remove(canonical)

            return {"success": True}

        except Exception as e:
            logger.error("Lỗi khi xóa: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # HELPER RENDER JS
    # -----------------------------------------------------------------
    def render_block(self, role: str = "assistant", btype: str = None, data: dict = None, **kwargs):
        payload = {"role": role, "data": data}
        if btype:
            payload["type"] = btype
        payload.update(kwargs)

        data = json.dumps(payload, ensure_ascii=False)
        self._eval_js(f"window.renderBlock && window.renderBlock({data})")

    def _eval_js(self, js_code: str):
        if self._window:
            try:
                self._window.evaluate_js(js_code)
            except Exception as e:
                logger.warning("Không thể thực thi JS: %s", e)
