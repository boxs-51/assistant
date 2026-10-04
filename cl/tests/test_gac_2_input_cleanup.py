import threading

from cl.src.game_automation.actions.action import (
    ActionIntent,
    ActionKind,
    ActionStatus,
)
from cl.src.game_automation.actions.focus_guard import GuardedTarget
from cl.src.game_automation.actions.keyboard import KeyboardExecutor
from cl.src.game_automation.actions.mouse import MouseExecutor
from cl.src.game_automation.actions.scheduler import ActionScheduler
from cl.src.game_automation.session.game_session import (
    GameSession,
    GameWindowIdentity,
)
from cl.src.game_automation.session.window_manager import CaptureGeometry


class KeyboardBackend:
    def __init__(self):
        self.events = []
        self.down_event = threading.Event()

    def key_down(self, key):
        self.events.append(("down", key))
        self.down_event.set()

    def key_up(self, key):
        self.events.append(("up", key))


class MouseBackend:
    def __init__(self):
        self.events = []

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def button_down(self, button):
        self.events.append(("down", button))

    def button_up(self, button):
        self.events.append(("up", button))


def test_keyboard_cleanup_releases_only_gac_owned_state():
    backend = KeyboardBackend()
    executor = KeyboardExecutor(backend)

    assert executor.key_up("user-held") is False
    executor.key_down("w")
    assert executor.owned_keys == frozenset({"w"})

    assert executor.cleanup() == ()
    assert executor.owned_keys == frozenset()
    assert backend.events == [("down", "w"), ("up", "w")]
    assert ("up", "user-held") not in backend.events


def test_mouse_cleanup_releases_only_gac_owned_state():
    backend = MouseBackend()
    executor = MouseExecutor(backend)

    assert executor.button_up("right") is False
    executor.button_down("left")
    assert executor.owned_buttons == frozenset({"left"})

    assert executor.cleanup() == ()
    assert executor.owned_buttons == frozenset()
    assert backend.events == [("down", "left"), ("up", "left")]
    assert ("up", "right") not in backend.events


class Guard:
    def __init__(self, session):
        self.session = session
        self.geometry = CaptureGeometry(left=0, top=0, width=100, height=100)

    def validate(
        self,
        *,
        automation_session_id,
        binding_generation,
        x=None,
        y=None,
    ):
        context = self.session.current_capture_context()
        assert context.automation_session_id == automation_session_id
        assert context.binding_generation == binding_generation
        return GuardedTarget(
            context=context,
            geometry=self.geometry,
            screen_x=x,
            screen_y=y,
        )


def test_scheduler_close_wakes_active_hold_and_cleans_owned_input():
    session = GameSession("close-cleanup")
    binding = session.bind(
        GameWindowIdentity(
            hwnd=11,
            process_id=22,
            process_start_time=33.0,
        )
    )
    keyboard_backend = KeyboardBackend()
    keyboard = KeyboardExecutor(keyboard_backend)
    scheduler = ActionScheduler(
        session,
        object(),
        focus_guard=Guard(session),
        keyboard=keyboard,
        mouse=MouseExecutor(MouseBackend()),
    )
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
    assert keyboard_backend.down_event.wait(timeout=0.5)
    scheduler.close()
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert outcomes[0].status in {
        ActionStatus.CANCELLED,
        ActionStatus.FAILED,
    }
    assert keyboard.owned_keys == frozenset()
    assert ("up", "w") in keyboard_backend.events
