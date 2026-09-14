import threading
import uuid
import json
import logging

logger = logging.getLogger(__name__)

class HitlManager:
    def __init__(self, eval_js_cb, execution_local, timeout=300.0):
        self._eval_js = eval_js_cb
        self._execution_local = execution_local
        self._approval_lock = threading.RLock()
        self._pending_approvals = {}
        self._timeout = timeout

    def respond(self, choice: bool, approval_id: str = None):
        selected_id = approval_id.strip() if isinstance(approval_id, str) else approval_id
        with self._approval_lock:
            if selected_id is None:
                if len(self._pending_approvals) != 1: return False
                selected_id = next(iter(self._pending_approvals))
            
            pending = self._pending_approvals.get(selected_id)
            if not pending: return False
            
            pending["response"] = choice in {True, "1", "true", "yes", "approve"} if isinstance(choice, str) else bool(choice)
            pending["event"].set()
            return True

    def show_dialog(self, req_data: dict) -> bool:
        req_data = dict(req_data) if isinstance(req_data, dict) else {"message": str(req_data)}
        approval_id = req_data.setdefault("approval_id", uuid.uuid4().hex)
        execution_id = getattr(self._execution_local, "execution_id", None)
        if execution_id: req_data.setdefault("execution_id", execution_id)

        event = threading.Event()
        pending = {"event": event, "response": None, "execution_id": execution_id}

        with self._approval_lock:
            self._pending_approvals[approval_id] = pending

        try:
            self._eval_js(f"window.showApprovalBar && window.showApprovalBar({json.dumps(req_data, ensure_ascii=False)})")
            completed = event.wait(timeout=self._timeout)
            
            with self._approval_lock:
                response = pending.get("response") if completed else False
                self._pending_approvals.pop(approval_id, None)

            self._eval_js(f"window.hideApprovalBar && window.hideApprovalBar({json.dumps(approval_id)})")
            return bool(response)
        except Exception:
            with self._approval_lock: self._pending_approvals.pop(approval_id, None)
            return False