import inspect
import threading

import pytest

from cl.src.game_automation.actions.action import (
    ActionIntent,
    ActionKind,
    ActionStatus,
)
from cl.src.game_automation.actions.cancellation import WaitResult
from cl.src.game_automation.actions.focus_guard import (
    GuardedTarget,
    TargetLostError,
)
from cl.src.game_automation.actions.keyboard import KeyboardExecutor
from cl.src.game_automation.actions.mouse import MouseExecutor
from cl.src.game_automation.actions.scheduler import (
    ActionScheduler,
    DuplicateSchedulerError,
)
from cl.src.game_automation.session.game_session import (
    GameSession,
    GameWindowIdentity,
)
from cl.src.game_automation.session.window_manager import CaptureGeometry


class FakeFocusGuard:
    def __init__(self, session, *, fail_on_call=None):
        self.session = session
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.geometry = CaptureGeometry(left=100, top=200, width=640, height=480)

    def validate(
        self,
        *,
        automation_session_id,
        binding_generation,
        x=None,
        y=None,
    ):
        self.calls += 1
        if self.fail_on_call is not None and self.calls >= self.fail_on_call:
            raise TargetLostError("target/focus lost")
        context = self.session.current_capture_context()
        if (
            context.automation_session_id != automation_session_id
            or context.binding_generation != binding_generation
        ):
            raise TargetLostError("stale action binding")
        if x is not None:
            if x < 0 or y < 0 or x >= self.geometry.width or y >= self.geometry.height:
                raise TargetLostError("out of bounds")
            return GuardedTarget(
                context=context,
                geometry=self.geometry,
                screen_x=self.geometry.left + x,
                screen_y=self.geometry.top + y,
            )
        return GuardedTarget(context=context, geometry=self.geometry)


class FakeKeyboardBackend:
    def __init__(self):
        self.events = []
        self.down_event = threading.Event()
        self.fail_down = False
        self.down_calls = 0

    def key_down(self, key):
        self.down_calls += 1
        if self.fail_down:
            raise RuntimeError("keyboard failure")
        self.events.append(("down", key))
        self.down_event.set()

    def key_up(self, key):
        self.events.append(("up", key))


class FakeMouseBackend:
    def __init__(self):
        self.events = []
        self.down_event = threading.Event()

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def button_down(self, button):
        self.events.append(("down", button))
        self.down_event.set()

    def button_up(self, button):
        self.events.append(("up", button))


def make_session(session_id):
    session = GameSession(session_id)
    binding = session.bind(
        GameWindowIdentity(
            hwnd=101,
            process_id=202,
            process_start_time=303.0,
        )
    )
    return session, binding


def make_scheduler(
    session,
    *,
    guard=None,
    keyboard_backend=None,
    mouse_backend=None,
    **kwargs,
):
    keyboard_backend = keyboard_backend or FakeKeyboardBackend()
    mouse_backend = mouse_backend or FakeMouseBackend()
    scheduler = ActionScheduler(
        session,
        object(),
        focus_guard=guard or FakeFocusGuard(session),
        keyboard=KeyboardExecutor(keyboard_backend),
        mouse=MouseExecutor(mouse_backend),
        **kwargs,
    )
    return scheduler, keyboard_backend, mouse_backend


def key_intent(session, binding, action_id="key", **kwargs):
    values = dict(
        action_id=action_id,
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.KEY_TAP,
        key="w",
    )
    values.update(kwargs)
    return ActionIntent(**values)

def mouse_hold_intent(session, binding, action_id="mouse-hold", **kwargs):
    values = dict(
        action_id=action_id,
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.MOUSE_HOLD,
        button="left",
        x=10,
        y=20,
        hold_seconds=5.0,
    )
    values.update(kwargs)
    return ActionIntent(**values)



def test_one_live_writer_per_session_but_different_sessions_are_independent():
    first_session, _ = make_session("writer-a")
    second_session, _ = make_session("writer-b")
    first, _, _ = make_scheduler(first_session)

    with pytest.raises(DuplicateSchedulerError):
        make_scheduler(first_session)

    second, _, _ = make_scheduler(second_session)
    second.close()
    first.close()

    replacement, _, _ = make_scheduler(first_session)
    replacement.close()


def test_key_tap_is_serialized_and_no_side_effect_retry_occurs():
    session, binding = make_session("single-attempt")
    keyboard = FakeKeyboardBackend()
    scheduler, _, _ = make_scheduler(session, keyboard_backend=keyboard)

    outcome = scheduler.execute(key_intent(session, binding))
    assert outcome.status is ActionStatus.SUCCEEDED
    assert keyboard.events == [("down", "w"), ("up", "w")]

    keyboard.fail_down = True
    failed = scheduler.execute(
        key_intent(session, binding, action_id="fail-once")
    )
    assert failed.status is ActionStatus.FAILED
    assert keyboard.down_calls == 2
    scheduler.close()


def test_cancellation_wakes_hold_and_releases_owned_key():
    session, binding = make_session("cancel-hold")
    keyboard = FakeKeyboardBackend()
    scheduler, _, _ = make_scheduler(session, keyboard_backend=keyboard)
    intent = ActionIntent(
        action_id="hold",
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.KEY_HOLD,
        key="w",
        hold_seconds=5.0,
    )
    outcomes = []
    thread = threading.Thread(
        target=lambda: outcomes.append(scheduler.execute(intent)),
        daemon=True,
    )

    thread.start()
    assert keyboard.down_event.wait(timeout=0.5)
    assert scheduler.cancel_current() is True
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert outcomes[0].status is ActionStatus.CANCELLED
    assert ("up", "w") in keyboard.events
    scheduler.close()


def test_emergency_stop_wakes_hold_is_latched_and_blocks_future_actions():
    session, binding = make_session("estop-hold")
    keyboard = FakeKeyboardBackend()
    scheduler, _, _ = make_scheduler(session, keyboard_backend=keyboard)
    hold = ActionIntent(
        action_id="hold",
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.KEY_HOLD,
        key="w",
        hold_seconds=5.0,
    )
    outcomes = []
    thread = threading.Thread(
        target=lambda: outcomes.append(scheduler.execute(hold)),
        daemon=True,
    )

    thread.start()
    assert keyboard.down_event.wait(timeout=0.5)
    assert scheduler.emergency_stop() is True
    assert scheduler.emergency_stop() is False
    thread.join(timeout=0.5)

    assert outcomes[0].status is ActionStatus.EMERGENCY_STOPPED
    assert scheduler.emergency_stop_latched is True
    assert ("up", "w") in keyboard.events

    event_count = len(keyboard.events)
    blocked = scheduler.execute(
        key_intent(session, binding, action_id="after-estop")
    )
    assert blocked.status is ActionStatus.EMERGENCY_STOPPED
    assert len(keyboard.events) == event_count
    scheduler.close()


def test_focus_loss_before_new_mouse_transition_sends_no_button_down():
    session, binding = make_session("focus-loss")
    guard = FakeFocusGuard(session, fail_on_call=3)
    scheduler, _keyboard, mouse = make_scheduler(session, guard=guard)
    intent = ActionIntent(
        action_id="click",
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.MOUSE_CLICK,
        button="left",
        x=10,
        y=20,
    )

    outcome = scheduler.execute(intent)

    assert outcome.status is ActionStatus.TARGET_LOST
    assert mouse.events == [("move", 110, 220)]
    scheduler.close()


def test_focus_loss_during_hold_releases_gac_owned_key():
    session, binding = make_session("hold-focus-loss")
    guard = FakeFocusGuard(session, fail_on_call=3)
    keyboard = FakeKeyboardBackend()
    scheduler, _, _ = make_scheduler(
        session,
        guard=guard,
        keyboard_backend=keyboard,
    )
    intent = ActionIntent(
        action_id="hold",
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.KEY_HOLD,
        key="w",
        hold_seconds=0.001,
    )

    outcome = scheduler.execute(intent)

    assert outcome.status is ActionStatus.TARGET_LOST
    assert keyboard.events == [("down", "w"), ("up", "w")]
    scheduler.close()


class FakeClock:
    def __init__(self, value=10.0):
        self.value = value

    def __call__(self):
        return self.value


def test_pacing_cooldown_and_deadline_use_injectable_monotonic_time():
    clock = FakeClock()

    def waiter(timeout, token, emergency, *, monotonic):
        assert monotonic is clock
        if emergency.is_triggered:
            return WaitResult.EMERGENCY_STOPPED
        if token.is_cancelled:
            return WaitResult.CANCELLED
        clock.value += timeout
        return WaitResult.COMPLETED

    session, binding = make_session("fake-clock")
    scheduler, _, _ = make_scheduler(
        session,
        base_action_delay=0.5,
        min_inter_action_interval=1.0,
        monotonic=clock,
        wait_function=waiter,
    )

    first = scheduler.execute(
        key_intent(
            session,
            binding,
            action_id="first",
            cooldown_key="attack",
            cooldown_seconds=2.0,
        )
    )
    assert first.status is ActionStatus.SUCCEEDED
    assert clock.value == pytest.approx(10.5)

    second = scheduler.execute(
        key_intent(
            session,
            binding,
            action_id="second",
            cooldown_key="attack",
        )
    )
    assert second.status is ActionStatus.SUCCEEDED
    assert clock.value == pytest.approx(12.5)
    scheduler.close()

    deadline_session, deadline_binding = make_session("deadline")
    deadline_scheduler, keyboard, _ = make_scheduler(
        deadline_session,
        base_action_delay=1.0,
        monotonic=clock,
        wait_function=waiter,
    )
    deadline = deadline_scheduler.execute(
        key_intent(
            deadline_session,
            deadline_binding,
            action_id="deadline",
            deadline_monotonic=clock.value + 0.25,
        )
    )
    assert deadline.status is ActionStatus.DEADLINE_EXCEEDED
    assert keyboard.events == []
    deadline_scheduler.close()


class SequencedGeometryGuard:
    def __init__(self, session, geometries):
        self.session = session
        self.geometries = list(geometries)
        self.calls = 0

    def validate(
        self,
        *,
        automation_session_id,
        binding_generation,
        x=None,
        y=None,
    ):
        context = self.session.current_capture_context()
        if (
            context.automation_session_id != automation_session_id
            or context.binding_generation != binding_generation
        ):
            raise TargetLostError("stale action binding")

        index = min(self.calls, len(self.geometries) - 1)
        geometry = self.geometries[index]
        self.calls += 1

        if x is None:
            return GuardedTarget(context=context, geometry=geometry)
        return GuardedTarget(
            context=context,
            geometry=geometry,
            screen_x=geometry.left + x,
            screen_y=geometry.top + y,
        )


def test_mouse_down_repositions_from_same_live_guard_geometry():
    session, binding = make_session("mouse-geometry")
    guard = SequencedGeometryGuard(
        session,
        [
            CaptureGeometry(left=100, top=200, width=640, height=480),
            CaptureGeometry(left=100, top=200, width=640, height=480),
            CaptureGeometry(left=100, top=200, width=640, height=480),
            CaptureGeometry(left=300, top=400, width=640, height=480),
            CaptureGeometry(left=300, top=400, width=640, height=480),
        ],
    )
    scheduler, _keyboard, mouse = make_scheduler(session, guard=guard)
    intent = ActionIntent(
        action_id="click-live-geometry",
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        kind=ActionKind.MOUSE_CLICK,
        button="left",
        x=10,
        y=20,
    )

    outcome = scheduler.execute(intent)

    assert outcome.status is ActionStatus.SUCCEEDED
    assert mouse.events == [
        ("move", 110, 220),
        ("move", 310, 420),
        ("down", "left"),
        ("up", "left"),
    ]
    scheduler.close()


class CommitBarrierScheduler(ActionScheduler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_entered = threading.Event()
        self.commit_release = threading.Event()

    def _finalize_outcome(
        self,
        intent,
        token,
        status,
        reason,
        completed,
    ):
        with self._transition_lock:
            self.commit_entered.set()
            assert self.commit_release.wait(timeout=1.0)
            return super()._finalize_outcome(
                intent,
                token,
                status,
                reason,
                completed,
            )


def test_cancel_cannot_be_accepted_after_success_commit_begins():
    session, binding = make_session("completion-linearization")
    keyboard = FakeKeyboardBackend()
    scheduler = CommitBarrierScheduler(
        session,
        object(),
        focus_guard=FakeFocusGuard(session),
        keyboard=KeyboardExecutor(keyboard),
        mouse=MouseExecutor(FakeMouseBackend()),
    )
    outcomes = []
    execute_thread = threading.Thread(
        target=lambda: outcomes.append(
            scheduler.execute(key_intent(session, binding))
        ),
        daemon=True,
    )
    execute_thread.start()

    assert scheduler.commit_entered.wait(timeout=0.5)

    cancel_results = []
    cancel_started = threading.Event()

    def cancel():
        cancel_started.set()
        cancel_results.append(scheduler.cancel_current())

    cancel_thread = threading.Thread(target=cancel, daemon=True)
    cancel_thread.start()
    assert cancel_started.wait(timeout=0.5)
    assert cancel_thread.is_alive() is True

    scheduler.commit_release.set()
    execute_thread.join(timeout=0.5)
    cancel_thread.join(timeout=0.5)

    assert execute_thread.is_alive() is False
    assert cancel_thread.is_alive() is False
    assert outcomes[0].status is ActionStatus.SUCCEEDED
    assert cancel_results == [False]
    scheduler.close()


def test_mouse_hold_cancel_releases_owned_button_and_stops_later_transitions():
    session, binding = make_session("mouse-cancel")
    mouse = FakeMouseBackend()
    scheduler, _keyboard, _ = make_scheduler(
        session,
        mouse_backend=mouse,
    )
    outcomes = []
    thread = threading.Thread(
        target=lambda: outcomes.append(
            scheduler.execute(mouse_hold_intent(session, binding))
        ),
        daemon=True,
    )
    thread.start()

    assert mouse.down_event.wait(timeout=0.5)
    assert scheduler.cancel_current() is True
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert outcomes[0].status is ActionStatus.CANCELLED
    assert mouse.events.count(("down", "left")) == 1
    assert mouse.events.count(("up", "left")) == 1
    assert mouse.events[-1] == ("up", "left")
    scheduler.close()


def test_mouse_hold_estop_releases_owned_button_and_latches():
    session, binding = make_session("mouse-estop")
    mouse = FakeMouseBackend()
    scheduler, _keyboard, _ = make_scheduler(
        session,
        mouse_backend=mouse,
    )
    outcomes = []
    thread = threading.Thread(
        target=lambda: outcomes.append(
            scheduler.execute(mouse_hold_intent(session, binding))
        ),
        daemon=True,
    )
    thread.start()

    assert mouse.down_event.wait(timeout=0.5)
    assert scheduler.emergency_stop() is True
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert outcomes[0].status is ActionStatus.EMERGENCY_STOPPED
    assert scheduler.emergency_stop_latched is True
    assert mouse.events.count(("down", "left")) == 1
    assert mouse.events.count(("up", "left")) == 1
    assert mouse.events[-1] == ("up", "left")
    scheduler.close()


def test_mouse_hold_target_failure_releases_owned_button():
    session, binding = make_session("mouse-target-failure")
    # Each new transition validates once before and once inside hold_current().
    # MOUSE_HOLD performs move (2), down-at-live-coordinate (2), then the
    # cleanup-oriented validation before release. Fail on that fifth guard.
    guard = FakeFocusGuard(session, fail_on_call=5)
    mouse = FakeMouseBackend()
    scheduler, _keyboard, _ = make_scheduler(
        session,
        guard=guard,
        mouse_backend=mouse,
    )
    intent = mouse_hold_intent(
        session,
        binding,
        action_id="mouse-target-failure",
        hold_seconds=0.001,
    )

    outcome = scheduler.execute(intent)

    assert outcome.status is ActionStatus.TARGET_LOST
    assert mouse.events.count(("down", "left")) == 1
    assert mouse.events.count(("up", "left")) == 1
    assert mouse.events[-1] == ("up", "left")
    scheduler.close()


class MutatingAfterValidationGuard(FakeFocusGuard):
    def __init__(self, session, mutation):
        super().__init__(session)
        self.mutation = mutation
        self.mutated = False

    def validate(
        self,
        *,
        automation_session_id,
        binding_generation,
        x=None,
        y=None,
    ):
        target = super().validate(
            automation_session_id=automation_session_id,
            binding_generation=binding_generation,
            x=x,
            y=y,
        )
        if not self.mutated:
            self.mutated = True
            if self.mutation == "rebind":
                self.session.bind(
                    GameWindowIdentity(
                        hwnd=202,
                        process_id=303,
                        process_start_time=404.0,
                    )
                )
            elif self.mutation == "unbind":
                self.session.unbind()
            else:
                self.session.close()
        return target


@pytest.mark.parametrize("mutation", ["rebind", "unbind", "close"])
def test_lifecycle_mutation_winning_before_hold_current_sends_no_new_input(
    mutation,
):
    session, binding = make_session(f"lifecycle-wins-{mutation}")
    keyboard = FakeKeyboardBackend()
    scheduler, _, _ = make_scheduler(
        session,
        guard=MutatingAfterValidationGuard(session, mutation),
        keyboard_backend=keyboard,
    )

    outcome = scheduler.execute(key_intent(session, binding))

    assert outcome.status is ActionStatus.TARGET_LOST
    assert keyboard.events == []
    assert keyboard.down_calls == 0
    scheduler.close()


class BlockingKeyboardBackend(FakeKeyboardBackend):
    def __init__(self):
        super().__init__()
        self.transition_entered = threading.Event()
        self.transition_release = threading.Event()

    def key_down(self, key):
        self.down_calls += 1
        self.events.append(("down", key))
        self.down_event.set()
        self.transition_entered.set()
        assert self.transition_release.wait(timeout=1.0)


@pytest.mark.parametrize("mutation", ["rebind", "unbind", "close"])
def test_hold_current_blocks_lifecycle_mutation_until_transition_exits(
    mutation,
):
    session, binding = make_session(f"transition-wins-{mutation}")
    keyboard = BlockingKeyboardBackend()
    scheduler, _, _ = make_scheduler(
        session,
        keyboard_backend=keyboard,
    )
    outcomes = []
    execute_thread = threading.Thread(
        target=lambda: outcomes.append(
            scheduler.execute(key_intent(session, binding))
        ),
        daemon=True,
    )
    execute_thread.start()
    assert keyboard.transition_entered.wait(timeout=0.5)

    mutation_started = threading.Event()
    mutation_completed = threading.Event()

    def mutate():
        mutation_started.set()
        if mutation == "rebind":
            session.bind(
                GameWindowIdentity(
                    hwnd=203,
                    process_id=304,
                    process_start_time=405.0,
                )
            )
        elif mutation == "unbind":
            session.unbind()
        else:
            session.close()
        mutation_completed.set()

    mutation_thread = threading.Thread(target=mutate, daemon=True)
    mutation_thread.start()
    assert mutation_started.wait(timeout=0.5)

    # The lifecycle mutation cannot complete while the one input transition
    # is inside GameSession.hold_current().
    assert mutation_completed.wait(timeout=0.05) is False

    keyboard.transition_release.set()
    assert mutation_completed.wait(timeout=0.5)
    mutation_thread.join(timeout=0.5)
    execute_thread.join(timeout=0.5)

    assert mutation_thread.is_alive() is False
    assert execute_thread.is_alive() is False
    assert keyboard.events.count(("down", "w")) == 1
    assert ("up", "w") in keyboard.events

    before = keyboard.down_calls
    stale = scheduler.execute(
        key_intent(session, binding, action_id=f"stale-after-{mutation}")
    )
    assert stale.status is ActionStatus.TARGET_LOST
    assert keyboard.down_calls == before
    scheduler.close()


def test_scheduler_consumes_public_lifecycle_guard_without_private_lock_or_wait():
    source = inspect.getsource(ActionScheduler._transition)

    assert "hold_current" in source
    assert "_session._lock" not in source
    assert "._lock.acquire" not in source
    assert "_wait_duration" not in source
    assert "_wait_for_pacing" not in source
    assert "wait_function" not in source
