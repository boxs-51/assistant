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


def test_identity_change_denies_visible_approval_and_rejects_late_response():
    harness = ApprovalHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)
    thread, result = _start_dialog(manager)
    old_id = harness.wait_for_show_count(1)["approval_id"]

    manager.invalidate_pending()
    assert manager.respond(True, old_id) is False
    thread.join(1.0)

    assert not thread.is_alive()
    assert result == [False]
    assert harness.hidden == [old_id]

    new_thread, new_result = _start_dialog(manager)
    new_id = harness.wait_for_show_count(2)["approval_id"]
    assert new_id != old_id
    assert manager.respond(True, old_id) is False
    assert manager.respond(True, new_id) is True
    new_thread.join(1.0)
    assert new_result == [True]


def test_queued_prompt_from_prior_auth_generation_cannot_appear_after_switch():
    harness = ApprovalHarness()
    local = SimpleNamespace(auth_generation=0)
    manager = HitlManager(harness, local, timeout=1.0)

    first_thread, first_result = _start_dialog(manager, {"name": "first"})
    first_id = harness.wait_for_show_count(1)["approval_id"]
    queued_thread, queued_result = _start_dialog(manager, {"name": "old-queued"})
    time.sleep(0.05)
    assert len(harness.shown) == 1

    manager.invalidate_pending()
    first_thread.join(1.0)
    queued_thread.join(1.0)
    assert not first_thread.is_alive()
    assert not queued_thread.is_alive()
    assert first_result == [False]
    assert queued_result == [False]
    assert len(harness.shown) == 1
    assert manager.respond(True, first_id) is False

    local.auth_generation = 1
    fresh_thread, fresh_result = _start_dialog(manager, {"name": "new"})
    new_id = harness.wait_for_show_count(2)["approval_id"]
    assert manager.respond(True, new_id) is True
    fresh_thread.join(1.0)
    assert fresh_result == [True]


class BlockingDisplayAckHarness(ApprovalHarness):
    """Reproduce an identity switch while WebView's show ACK is pending."""

    def __init__(self):
        super().__init__()
        self.ack_release = threading.Event()

    def __call__(self, js_code):
        show = re.search(r"showApprovalBar\((.*)\)$", js_code)
        if show:
            with self._condition:
                request = json.loads(show.group(1))
                self.shown.append(request)
                self._condition.notify_all()
            if not self.ack_release.wait(2.0):
                raise AssertionError("timed out waiting to release display ACK")
            return request["approval_id"]
        return super().__call__(js_code)


def test_identity_change_during_delayed_display_ack_hides_old_bar():
    harness = BlockingDisplayAckHarness()
    manager = HitlManager(harness, SimpleNamespace(), timeout=1.0)
    thread, result = _start_dialog(manager)
    request = harness.wait_for_show_count(1)
    old_id = request["approval_id"]

    manager.invalidate_pending()
    assert manager.respond(True, old_id) is False
    harness.ack_release.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert result == [False]
    assert harness.hidden == [old_id]
    assert manager._pending_approvals == {}


class BlockingHideHarness(ApprovalHarness):
    def __init__(self):
        super().__init__()
        self.hide_entered = threading.Event()
        self.hide_release = threading.Event()

    def __call__(self, js_code):
        if "window.hideApprovalBar" in js_code:
            self.hide_entered.set()
            assert self.hide_release.wait(2.0)
        return super().__call__(js_code)


def test_identity_revoke_while_hide_is_blocked_cannot_return_cached_approval():
    harness = BlockingHideHarness()
    manager = HitlManager(harness, SimpleNamespace(auth_generation=0), timeout=1.0,
                          require_origin_pin=True)
    thread, result = _start_dialog(manager)
    old_id = harness.wait_for_show_count(1)["approval_id"]
    assert manager.respond(True, old_id) is True
    assert harness.hide_entered.wait(1.0)

    # The decision was removed from pending, but its effect authority must
    # still disappear while the WebView hide callback is blocked.
    manager.invalidate_pending()
    harness.hide_release.set()
    thread.join(1.0)
    assert not thread.is_alive()
    assert result == [False]
    assert harness.hidden == [old_id]


def test_strict_production_hitl_requires_captured_origin_generation():
    harness = ApprovalHarness()
    execution_local = SimpleNamespace()
    manager = HitlManager(harness, execution_local, timeout=1.0,
                          require_origin_pin=True)
    assert manager.show_dialog({"name": "untrusted-dispatcher"}) is False
    assert harness.shown == []

    # A correctly pinned generation remains usable with a fresh token.
    execution_local.auth_generation = 0
    thread, result = _start_dialog(manager)
    approval_id = harness.wait_for_show_count(1)["approval_id"]
    assert manager.respond(True, approval_id) is True
    thread.join(1.0)
    assert result == [True]


def test_old_pinned_invocation_cannot_adopt_new_generation():
    harness = ApprovalHarness()
    execution_local = SimpleNamespace(auth_generation=0)
    manager = HitlManager(harness, execution_local, timeout=1.0,
                          require_origin_pin=True)
    manager.invalidate_pending()
    assert manager.show_dialog({"name": "stale"}) is False
    assert harness.shown == []
