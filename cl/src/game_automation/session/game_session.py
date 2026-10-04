from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import threading
import uuid


class GameSessionError(RuntimeError):
    """Base error for GAC-1 local game-session lifecycle failures."""


class SessionClosedError(GameSessionError):
    """Raised when an operation targets a terminally closed session."""


class UnboundSessionError(GameSessionError):
    """Raised when capture is requested without an authoritative binding."""


class StaleCaptureContextError(GameSessionError):
    """Raised when capture evidence no longer belongs to the current binding."""


class GameSessionState(str, Enum):
    UNBOUND = "UNBOUND"
    BOUND = "BOUND"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class GameWindowIdentity:
    """Immutable native target identity used for binding authority.

    Human-facing metadata such as title/executable is intentionally not part of
    the authority decision. HWND, PID and process start time must all continue
    to identify the same native target.
    """

    hwnd: int
    process_id: int
    process_start_time: float
    title: str = ""
    executable: str | None = None

    def __post_init__(self) -> None:
        if self.hwnd <= 0:
            raise ValueError("hwnd must be a positive native window handle")
        if self.process_id <= 0:
            raise ValueError("process_id must be positive")
        if not math.isfinite(self.process_start_time) or self.process_start_time < 0:
            raise ValueError("process_start_time must be a finite non-negative value")


@dataclass(frozen=True, slots=True)
class GameWindowBinding:
    identity: GameWindowIdentity
    generation: int

    def __post_init__(self) -> None:
        if self.generation <= 0:
            raise ValueError("binding generation must be positive")


@dataclass(frozen=True, slots=True)
class CaptureContext:
    automation_session_id: str
    binding_generation: int
    identity: GameWindowIdentity

    def __post_init__(self) -> None:
        if not self.automation_session_id:
            raise ValueError("automation_session_id is required")
        if self.binding_generation <= 0:
            raise ValueError("binding_generation must be positive")


class GameSession:
    """Thread-safe local ownership for one selected game-window binding.

    This class deliberately owns only local capture/session lifecycle. It does
    not model Agent execution, remote invocation recovery, input/action state,
    transport reconnect, or durable server authority.
    """

    def __init__(self, automation_session_id: str | None = None) -> None:
        self._automation_session_id = (
            uuid.uuid4().hex
            if automation_session_id is None
            else automation_session_id
        )
        if not self._automation_session_id:
            raise ValueError("automation_session_id must not be empty")

        self._lock = threading.RLock()
        self._state = GameSessionState.UNBOUND
        self._generation = 0
        self._binding: GameWindowBinding | None = None
        self._frame_sequence = 0

    @property
    def automation_session_id(self) -> str:
        return self._automation_session_id

    @property
    def state(self) -> GameSessionState:
        with self._lock:
            return self._state

    @property
    def binding_generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def binding(self) -> GameWindowBinding | None:
        with self._lock:
            return self._binding

    def bind(self, identity: GameWindowIdentity) -> GameWindowBinding:
        """Bind/rebind to exactly one immutable native target identity."""

        with self._lock:
            self._require_not_closed()
            self._generation += 1
            self._frame_sequence = 0
            self._binding = GameWindowBinding(
                identity=identity,
                generation=self._generation,
            )
            self._state = GameSessionState.BOUND
            return self._binding

    def unbind(self) -> int:
        """Invalidate the current binding and every older capture context."""

        with self._lock:
            self._require_not_closed()
            self._generation += 1
            self._frame_sequence = 0
            self._binding = None
            self._state = GameSessionState.UNBOUND
            return self._generation

    def close(self) -> int:
        """Terminally invalidate this local session.

        Close is idempotent. The first close advances the generation so any
        in-flight capture context is stale before callers observe CLOSED.
        """

        with self._lock:
            if self._state is not GameSessionState.CLOSED:
                self._generation += 1
                self._frame_sequence = 0
                self._binding = None
                self._state = GameSessionState.CLOSED
            return self._generation

    def current_capture_context(self) -> CaptureContext:
        with self._lock:
            if self._state is GameSessionState.CLOSED:
                raise SessionClosedError("game session is closed")
            if self._state is not GameSessionState.BOUND or self._binding is None:
                raise UnboundSessionError("game session has no bound window")
            return CaptureContext(
                automation_session_id=self._automation_session_id,
                binding_generation=self._binding.generation,
                identity=self._binding.identity,
            )

    def assert_current(self, context: CaptureContext) -> None:
        """Reject a capture context that no longer owns the current binding."""

        with self._lock:
            if not self._is_current_locked(context):
                raise StaleCaptureContextError(
                    "capture context does not match the current game-window binding"
                )

    def is_current(self, context: CaptureContext) -> bool:
        with self._lock:
            return self._is_current_locked(context)

    def reserve_frame_sequence(self, context: CaptureContext) -> int:
        """Allocate the next frame sequence only for a still-current binding."""

        with self._lock:
            if not self._is_current_locked(context):
                raise StaleCaptureContextError(
                    "cannot publish a frame for a stale game-window binding"
                )
            self._frame_sequence += 1
            return self._frame_sequence

    def _is_current_locked(self, context: CaptureContext) -> bool:
        binding = self._binding
        return (
            self._state is GameSessionState.BOUND
            and binding is not None
            and context.automation_session_id == self._automation_session_id
            and context.binding_generation == binding.generation
            and context.identity == binding.identity
        )

    def _require_not_closed(self) -> None:
        if self._state is GameSessionState.CLOSED:
            raise SessionClosedError("game session is closed")
