from __future__ import annotations

import math
import threading
import time
from typing import Callable

from .action import ActionIntent, ActionKind, ActionOutcome, ActionStatus
from .cancellation import (
    CancellationToken,
    EmergencyStopLatch,
    WaitResult,
    WakeSignal,
    wait_interruptibly,
)
from .focus_guard import (
    FocusGuard,
    FocusGuardError,
    GuardedTarget,
    TargetLostError,
)
from .keyboard import KeyboardExecutor
from .mouse import MouseExecutor
from ..session.game_session import GameSession, GameSessionError
from ..session.window_manager import GameWindowManager


class SchedulerError(RuntimeError):
    """Base error for local GAC-2 scheduler lifecycle failures."""


class DuplicateSchedulerError(SchedulerError):
    """Raised when a second writer claims the same automation session."""


class SchedulerClosedError(SchedulerError):
    """Raised when execution is requested after scheduler close."""


class ActionCancelledError(SchedulerError):
    pass


class EmergencyStoppedError(SchedulerError):
    pass


class ActionDeadlineExceeded(SchedulerError):
    pass


WaitFunction = Callable[..., WaitResult]


class ActionScheduler:
    """Serialized, process-local action writer for exactly one game session."""

    _registry_lock = threading.Lock()
    _active_sessions: set[str] = set()

    def __init__(
        self,
        session: GameSession,
        window_manager: GameWindowManager,
        *,
        focus_guard: FocusGuard | None = None,
        keyboard: KeyboardExecutor | None = None,
        mouse: MouseExecutor | None = None,
        base_action_delay: float = 0.0,
        min_inter_action_interval: float = 0.0,
        monotonic=time.monotonic,
        wait_function: WaitFunction = wait_interruptibly,
    ) -> None:
        for name, value in (
            ("base_action_delay", base_action_delay),
            ("min_inter_action_interval", min_inter_action_interval),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")

        self._session = session
        self._session_id = session.automation_session_id
        self._window_manager = window_manager
        self._focus_guard = focus_guard or FocusGuard(session, window_manager)
        self._keyboard = keyboard or KeyboardExecutor()
        self._mouse = mouse or MouseExecutor()
        self._base_action_delay = base_action_delay
        self._min_inter_action_interval = min_inter_action_interval
        self._monotonic = monotonic
        self._wait_function = wait_function

        self._signal = WakeSignal()
        self._emergency_stop = EmergencyStopLatch(self._signal)
        self._execute_lock = threading.Lock()
        self._transition_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._current_token: CancellationToken | None = None
        self._last_completed: float | None = None
        self._cooldown_until: dict[str, float] = {}
        self._closed = False

        with self._registry_lock:
            if self._session_id in self._active_sessions:
                raise DuplicateSchedulerError(
                    "automation session already has a live GAC-2 writer"
                )
            self._active_sessions.add(self._session_id)

    @property
    def automation_session_id(self) -> str:
        return self._session_id

    @property
    def emergency_stop_latched(self) -> bool:
        return self._emergency_stop.is_triggered

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    def execute(self, intent: ActionIntent) -> ActionOutcome:
        if intent.automation_session_id != self._session_id:
            raise ValueError("action targets a different automation session")

        with self._execute_lock:
            with self._state_lock:
                if self._closed:
                    raise SchedulerClosedError("action scheduler is closed")
                token = CancellationToken(self._signal)
                self._current_token = token

            started = self._monotonic()
            status = ActionStatus.FAILED
            reason: str | None = None

            try:
                self._raise_if_stopped(intent, token)
                self._wait_for_pacing(intent, token)
                self._raise_if_stopped(intent, token)
                self._perform(intent, token)
                status = ActionStatus.SUCCEEDED
            except EmergencyStoppedError as error:
                status = ActionStatus.EMERGENCY_STOPPED
                reason = str(error)
            except ActionCancelledError as error:
                status = ActionStatus.CANCELLED
                reason = str(error)
            except ActionDeadlineExceeded as error:
                status = ActionStatus.DEADLINE_EXCEEDED
                reason = str(error)
            except FocusGuardError as error:
                status = ActionStatus.TARGET_LOST
                reason = str(error)
            except Exception as error:
                status = ActionStatus.FAILED
                reason = str(error) or error.__class__.__name__

            cleanup_failures = self._cleanup_owned_input()
            completed = self._monotonic()
            if cleanup_failures:
                cleanup_reason = (
                    "owned input cleanup failed: "
                    + ", ".join(cleanup_failures)
                )
                if status is ActionStatus.SUCCEEDED:
                    status = ActionStatus.FAILED
                    reason = cleanup_reason
                else:
                    reason = (
                        cleanup_reason
                        if reason is None
                        else f"{reason}; {cleanup_reason}"
                    )

            status, reason = self._finalize_outcome(
                intent,
                token,
                status,
                reason,
                completed,
            )

            return ActionOutcome(
                action_id=intent.action_id,
                status=status,
                started_monotonic=started,
                completed_monotonic=completed,
                failure_reason=reason,
            )

    def cancel_current(self) -> bool:
        """Cancel only the current action; future actions remain allowed."""

        with self._transition_lock:
            with self._state_lock:
                token = self._current_token
            if token is None:
                return False
            changed = token.cancel()
            self._cleanup_owned_input()
            return changed

    def emergency_stop(self) -> bool:
        """Latch terminal local stop and release GAC-owned input best-effort."""

        with self._transition_lock:
            changed = self._emergency_stop.trigger()
            self._cleanup_owned_input()
            return changed

    def close(self) -> None:
        """Close this writer and release its registry lease only after drain.

        The transition lock is intentionally released before waiting for the
        execute lock: active execution finalization needs the transition lock.
        Keeping the registry claim until execute/final cleanup has drained
        prevents a replacement writer from racing a late cleanup release.
        """

        with self._transition_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                token = self._current_token
            if token is not None:
                token.cancel()
            self._cleanup_owned_input()

        # Do not hold _transition_lock while waiting here. The active execute()
        # path owns _execute_lock for its full lifetime and may need
        # _transition_lock to finalize its outcome.
        with self._execute_lock:
            # One final best-effort retry happens after all active execution
            # cleanup has drained. Once the registry lease is released below,
            # this scheduler has no remaining path that can emit cleanup input.
            self._cleanup_owned_input()
            with self._registry_lock:
                self._active_sessions.discard(self._session_id)

    def __enter__(self) -> "ActionScheduler":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _wait_for_pacing(
        self,
        intent: ActionIntent,
        token: CancellationToken,
    ) -> None:
        now = self._monotonic()
        earliest = now + self._base_action_delay
        with self._state_lock:
            if self._last_completed is not None:
                earliest = max(
                    earliest,
                    self._last_completed + self._min_inter_action_interval,
                )
            if intent.cooldown_key is not None:
                earliest = max(
                    earliest,
                    self._cooldown_until.get(intent.cooldown_key, now),
                )

        self._wait_duration(max(0.0, earliest - now), intent, token)

    def _wait_duration(
        self,
        duration: float,
        intent: ActionIntent,
        token: CancellationToken,
    ) -> None:
        if duration <= 0:
            self._raise_if_stopped(intent, token)
            return

        self._raise_if_stopped(intent, token)
        allowed = duration
        deadline_limited = False
        if intent.deadline_monotonic is not None:
            remaining = intent.deadline_monotonic - self._monotonic()
            if remaining <= 0:
                raise ActionDeadlineExceeded("local action deadline exceeded")
            if remaining < allowed:
                allowed = remaining
                deadline_limited = True

        result = self._wait_function(
            allowed,
            token,
            self._emergency_stop,
            monotonic=self._monotonic,
        )
        if result is WaitResult.EMERGENCY_STOPPED:
            raise EmergencyStoppedError("emergency stop is latched")
        if result is WaitResult.CANCELLED:
            raise ActionCancelledError("current action was cancelled")
        if deadline_limited:
            raise ActionDeadlineExceeded("local action deadline exceeded")
        self._raise_if_stopped(intent, token)

    def _perform(self, intent: ActionIntent, token: CancellationToken) -> None:
        if intent.kind is ActionKind.KEY_TAP:
            assert intent.key is not None
            self._transition(
                intent,
                token,
                lambda _target: self._keyboard.key_down(intent.key),
            )
            self._validate_only(intent, token)
            self._keyboard.key_up(intent.key)
            self._raise_if_stopped(intent, token)
            return

        if intent.kind is ActionKind.KEY_HOLD:
            assert intent.key is not None
            self._transition(
                intent,
                token,
                lambda _target: self._keyboard.key_down(intent.key),
            )
            self._wait_duration(intent.hold_seconds, intent, token)
            self._validate_only(intent, token)
            self._keyboard.key_up(intent.key)
            self._raise_if_stopped(intent, token)
            return

        if intent.kind is ActionKind.MOUSE_MOVE:
            self._transition(
                intent,
                token,
                lambda target: self._mouse.move_to(
                    self._screen_x(target),
                    self._screen_y(target),
                ),
                x=intent.x,
                y=intent.y,
            )
            return

        if intent.kind is ActionKind.MOUSE_CLICK:
            assert intent.button is not None
            self._transition(
                intent,
                token,
                lambda target: self._mouse.move_to(
                    self._screen_x(target),
                    self._screen_y(target),
                ),
                x=intent.x,
                y=intent.y,
            )
            self._transition(
                intent,
                token,
                lambda target: self._mouse.button_down_at(
                    intent.button,
                    self._screen_x(target),
                    self._screen_y(target),
                ),
                x=intent.x,
                y=intent.y,
            )
            self._validate_only(intent, token, x=intent.x, y=intent.y)
            self._mouse.button_up(intent.button)
            self._raise_if_stopped(intent, token)
            return

        if intent.kind is ActionKind.MOUSE_HOLD:
            assert intent.button is not None
            self._transition(
                intent,
                token,
                lambda target: self._mouse.move_to(
                    self._screen_x(target),
                    self._screen_y(target),
                ),
                x=intent.x,
                y=intent.y,
            )
            self._transition(
                intent,
                token,
                lambda target: self._mouse.button_down_at(
                    intent.button,
                    self._screen_x(target),
                    self._screen_y(target),
                ),
                x=intent.x,
                y=intent.y,
            )
            self._wait_duration(intent.hold_seconds, intent, token)
            self._validate_only(intent, token, x=intent.x, y=intent.y)
            self._mouse.button_up(intent.button)
            self._raise_if_stopped(intent, token)
            return

        raise ValueError(f"unsupported action kind: {intent.kind}")

    def _transition(
        self,
        intent: ActionIntent,
        token: CancellationToken,
        callback: Callable[[GuardedTarget], None],
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Gate each new side effect against stop state + exact live target."""

        with self._transition_lock:
            self._raise_if_stopped(intent, token)
            target = self._focus_guard.validate(
                automation_session_id=intent.automation_session_id,
                binding_generation=intent.binding_generation,
                x=x,
                y=y,
            )
            self._raise_if_stopped(intent, token)

            try:
                with self._session.hold_current(target.context):
                    # Revalidate exact target/focus/geometry after lifecycle
                    # stabilization, then emit exactly one bounded transition.
                    # No pacing/hold/cooldown wait is allowed inside this guard.
                    target = self._focus_guard.validate(
                        automation_session_id=intent.automation_session_id,
                        binding_generation=intent.binding_generation,
                        x=x,
                        y=y,
                    )
                    self._raise_if_stopped(intent, token)
                    callback(target)
            except GameSessionError as error:
                raise TargetLostError(str(error)) from error

    def _validate_only(
        self,
        intent: ActionIntent,
        token: CancellationToken,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        with self._transition_lock:
            self._raise_if_stopped(intent, token)
            self._focus_guard.validate(
                automation_session_id=intent.automation_session_id,
                binding_generation=intent.binding_generation,
                x=x,
                y=y,
            )
            self._raise_if_stopped(intent, token)

    def _raise_if_stopped(
        self,
        intent: ActionIntent,
        token: CancellationToken,
    ) -> None:
        with self._state_lock:
            if self._closed:
                raise SchedulerClosedError("action scheduler is closed")
        if self._emergency_stop.is_triggered:
            raise EmergencyStoppedError("emergency stop is latched")
        if token.is_cancelled:
            raise ActionCancelledError("current action was cancelled")
        if (
            intent.deadline_monotonic is not None
            and self._monotonic() >= intent.deadline_monotonic
        ):
            raise ActionDeadlineExceeded("local action deadline exceeded")

    def _finalize_outcome(
        self,
        intent: ActionIntent,
        token: CancellationToken,
        status: ActionStatus,
        reason: str | None,
        completed: float,
    ) -> tuple[ActionStatus, str | None]:
        """Linearize outcome commitment against cancel/e-stop acceptance.

        cancel_current() uses the same transition lock. If it returns True
        before this method commits, the token is observed as cancelled and the
        outcome cannot be SUCCEEDED. Once success is committed, the current
        token is cleared before the lock is released, so later cancellation
        returns False.
        """

        with self._transition_lock:
            if self._emergency_stop.is_triggered:
                status = ActionStatus.EMERGENCY_STOPPED
                reason = "emergency stop is latched"
            elif token.is_cancelled:
                status = ActionStatus.CANCELLED
                reason = "current action was cancelled"
            elif status is ActionStatus.SUCCEEDED:
                try:
                    self._raise_if_stopped(intent, token)
                except EmergencyStoppedError as error:
                    status = ActionStatus.EMERGENCY_STOPPED
                    reason = str(error)
                except ActionCancelledError as error:
                    status = ActionStatus.CANCELLED
                    reason = str(error)
                except ActionDeadlineExceeded as error:
                    status = ActionStatus.DEADLINE_EXCEEDED
                    reason = str(error)
                except SchedulerClosedError as error:
                    status = ActionStatus.FAILED
                    reason = str(error)

            with self._state_lock:
                if self._current_token is token:
                    self._current_token = None
                self._last_completed = completed
                if (
                    status is ActionStatus.SUCCEEDED
                    and intent.cooldown_key is not None
                    and intent.cooldown_seconds > 0
                ):
                    self._cooldown_until[intent.cooldown_key] = (
                        completed + intent.cooldown_seconds
                    )

        return status, reason

    def _cleanup_owned_input(self) -> tuple[str, ...]:
        failures = [
            f"key:{key}"
            for key in self._keyboard.cleanup()
        ]
        failures.extend(
            f"button:{button}"
            for button in self._mouse.cleanup()
        )
        return tuple(failures)

    @staticmethod
    def _screen_x(target: GuardedTarget) -> int:
        if target.screen_x is None:
            raise RuntimeError("guarded mouse target has no screen_x")
        return target.screen_x

    @staticmethod
    def _screen_y(target: GuardedTarget) -> int:
        if target.screen_y is None:
            raise RuntimeError("guarded mouse target has no screen_y")
        return target.screen_y
