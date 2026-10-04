import json
import re
import threading
import time
from types import SimpleNamespace

from cl.src.ui.hitl import HitlManager


class ApprovalHarness:
    def __init__(self, fail_show=False, wrong_show_ack=False):
        self.fail_show = fail_show
        self.wrong_show_ack = wrong_show_ack
        self._condition = threading.Condition()
        self.shown = []
        self.hidden = []

    def __call__(self, js_code):
        show = re.search(r"showApprovalBar\((.*)\)$", js_code)
        hide = re.search(r"hideApprovalBar\((.*)\)$", js_code)
        with self._condition:
            if show:
                payload = json.loads(show.group(1))
                self.shown.append(payload)
                self._condition.notify_all()
                if self.fail_show:
                    return False
                if self.wrong_show_ack:
                    return "stale-approval-id"
                return payload["approval_id"]
            if hide:
                self.hidden.append(json.loads(hide.group(1)))
                self._condition.notify_all()
                return True
        return True

    def wait_for_show_count(self, count, timeout=1.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.shown) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"expected {count} shown approvals, got {len(self.shown)}")
                self._condition.wait(remaining)
            return self.shown[count - 1]


def _start_dialog(manager, payload=None):
    result = []
    thread = threading.Thread(
        target=lambda: result.append(manager.show_dialog(payload or {"name": "tool"})),
        daemon=True,
    )
    thread.start()
    return thread, result


def test_exact_approval_id_round_trip_approve_and_reject():
    for choice, expected in [(True, True), (False, False)]:
        harness = ApprovalHarness()
        manager = HitlManager(harness, SimpleNamespace(execution_id="exec-1"), timeout=1.0)
        thread, result = _start_dialog(manager)
        request = harness.wait_for_show_count(1)
        approval_id = request["approval_id"]

        assert request["execution_id"] == "exec-1"
        assert manager.respond(choice, approval_id) is True
        thread.join(1.0)

        assert not thread.is_alive()
        assert result == [expected]
        assert harness.hidden == [approval_id]
        assert manager.respond(True, approval_id) is False


def test_wrong_id_and_missing_id_fail_closed_without_consuming_active_approval():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)
    thread, result = _start_dialog(manager)
    request = harness.wait_for_show_count(1)
    approval_id = request["approval_id"]

    assert manager.respond(True, None) is False
    assert manager.respond(True, "") is False
    assert manager.respond(True, "wrong-id") is False
    assert manager.respond(True, approval_id) is True

    thread.join(1.0)
    assert result == [True]


def test_first_terminal_response_wins():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)
    thread, result = _start_dialog(manager)
    approval_id = harness.wait_for_show_count(1)["approval_id"]

    assert manager.respond(True, approval_id) is True
    assert manager.respond(False, approval_id) is False

    thread.join(1.0)
    assert result == [True]


def test_single_visible_approval_surface_is_serialized():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)

    first_thread, first_result = _start_dialog(manager, {"name": "first"})
    first = harness.wait_for_show_count(1)

    second_thread, second_result = _start_dialog(manager, {"name": "second"})
    time.sleep(0.05)
    assert len(harness.shown) == 1

    assert manager.respond(True, first["approval_id"]) is True
    first_thread.join(1.0)
    assert first_result == [True]

    second = harness.wait_for_show_count(2)
    assert second["approval_id"] != first["approval_id"]
    assert manager.respond(False, second["approval_id"]) is True
    second_thread.join(1.0)
    assert second_result == [False]
    assert harness.hidden == [first["approval_id"], second["approval_id"]]


def test_timeout_fails_closed_and_stale_response_is_rejected():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=0.02)
    thread, result = _start_dialog(manager)
    request = harness.wait_for_show_count(1)

    thread.join(1.0)
    assert result == [False]
    assert harness.hidden == [request["approval_id"]]
    assert manager.respond(True, request["approval_id"]) is False


def test_display_failure_fails_closed_immediately():
    harness = ApprovalHarness(fail_show=True)
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)

    started = time.monotonic()
    result = manager.show_dialog({"name": "tool"})
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 0.5
    assert len(harness.shown) == 1
    assert harness.hidden == []
    approval_id = harness.shown[0]["approval_id"]
    assert manager.respond(True, approval_id) is False


def test_wrong_display_ack_id_fails_closed_immediately():
    harness = ApprovalHarness(wrong_show_ack=True)
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)

    started = time.monotonic()
    result = manager.show_dialog({"name": "tool"})
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 0.5
    assert len(harness.shown) == 1
    assert harness.hidden == []
    approval_id = harness.shown[0]["approval_id"]
    assert manager.respond(True, approval_id) is False


def test_caller_supplied_approval_id_is_not_trusted():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)
    thread, result = _start_dialog(manager, {"approval_id": "attacker-controlled"})
    request = harness.wait_for_show_count(1)

    assert request["approval_id"] != "attacker-controlled"
    assert manager.respond(True, request["approval_id"]) is True
    thread.join(1.0)
    assert result == [True]
