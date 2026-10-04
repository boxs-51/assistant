import json
import logging
import threading
import uuid

logger = logging.getLogger(__name__)


class HitlManager:
    def __init__(self, eval_js_cb, execution_local, timeout=300.0):
        self._eval_js = eval_js_cb
        self._execution_local = execution_local
        self._approval_lock = threading.RLock()
        self._approval_ui_lock = threading.Lock()
        self._pending_approvals = {}
        self._timeout = timeout

    @staticmethod
    def _coerce_choice(choice) -> bool:
        if isinstance(choice, str):
            return choice.strip().lower() in {"1", "true", "yes", "approve"}
        return bool(choice)

    def respond(self, choice: bool, approval_id: str = None):
        selected_id = approval_id.strip() if isinstance(approval_id, str) else approval_id
        if not selected_id:
            return False

        with self._approval_lock:
            pending = self._pending_approvals.get(selected_id)
            if not pending or pending["resolved"]:
                return False

            pending["resolved"] = True
            pending["response"] = self._coerce_choice(choice)
            pending["event"].set()
            return True

    def show_dialog(self, req_data: dict) -> bool:
        request = (
            dict(req_data)
            if isinstance(req_data, dict)
            else {"message": str(req_data)}
        )
        approval_id = uuid.uuid4().hex
        request["approval_id"] = approval_id

        execution_id = getattr(self._execution_local, "execution_id", None)
        if execution_id:
            request["execution_id"] = execution_id

        with self._approval_ui_lock:
            event = threading.Event()
            pending = {
                "event": event,
                "response": None,
                "resolved": False,
                "execution_id": execution_id,
            }

            with self._approval_lock:
                self._pending_approvals[approval_id] = pending

            displayed_id = self._eval_js(
                "window.showApprovalBar && window.showApprovalBar("
                + json.dumps(request, ensure_ascii=False)
                + ")"
            )
            if displayed_id != approval_id:
                with self._approval_lock:
                    self._pending_approvals.pop(approval_id, None)
                logger.warning(
                    "HITL approval UI display acknowledgement mismatch; "
                    "failing closed: expected=%s actual=%r",
                    approval_id,
                    displayed_id,
                )
                return False

            completed = event.wait(timeout=self._timeout)

            with self._approval_lock:
                response = pending.get("response") if completed else False
                self._pending_approvals.pop(approval_id, None)

            hidden = self._eval_js(
                "window.hideApprovalBar && window.hideApprovalBar("
                + json.dumps(approval_id)
                + ")"
            )
            if hidden is not True:
                logger.warning(
                    "HITL approval UI hide dispatch failed: %s",
                    approval_id,
                )

            return bool(response)
