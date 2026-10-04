from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class ActionKind(str, Enum):
    KEY_TAP = "KEY_TAP"
    KEY_HOLD = "KEY_HOLD"
    MOUSE_MOVE = "MOUSE_MOVE"
    MOUSE_CLICK = "MOUSE_CLICK"
    MOUSE_HOLD = "MOUSE_HOLD"


class ActionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    TARGET_LOST = "TARGET_LOST"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """One bounded process-local input attempt.

    The contract is intentionally client-internal. It is not a capability DTO,
    durable Agent execution state, UBQ reservation, or server timeout object.
    """

    action_id: str
    automation_session_id: str
    binding_generation: int
    kind: ActionKind
    key: str | None = None
    button: str | None = None
    x: int | None = None
    y: int | None = None
    hold_seconds: float = 0.0
    deadline_monotonic: float | None = None
    cooldown_key: str | None = None
    cooldown_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id.strip():
            raise ValueError("action_id must be a non-empty string")
        if (
            not isinstance(self.automation_session_id, str)
            or not self.automation_session_id.strip()
        ):
            raise ValueError("automation_session_id must be a non-empty string")
        if self.binding_generation <= 0:
            raise ValueError("binding_generation must be positive")

        try:
            kind = (
                self.kind
                if isinstance(self.kind, ActionKind)
                else ActionKind(self.kind)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("unsupported action kind") from error
        object.__setattr__(self, "kind", kind)

        if not math.isfinite(self.hold_seconds) or self.hold_seconds < 0:
            raise ValueError("hold_seconds must be finite and non-negative")
        if (
            self.deadline_monotonic is not None
            and (
                not math.isfinite(self.deadline_monotonic)
                or self.deadline_monotonic <= 0
            )
        ):
            raise ValueError(
                "deadline_monotonic must be a finite positive monotonic timestamp"
            )
        if not math.isfinite(self.cooldown_seconds) or self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be finite and non-negative")
        if self.cooldown_seconds > 0 and not self.cooldown_key:
            raise ValueError("positive cooldown_seconds requires cooldown_key")
        if self.cooldown_key is not None and not self.cooldown_key.strip():
            raise ValueError("cooldown_key must not be empty")

        if kind in (ActionKind.KEY_TAP, ActionKind.KEY_HOLD):
            if not isinstance(self.key, str) or not self.key.strip():
                raise ValueError("keyboard action requires key")
            if any(value is not None for value in (self.button, self.x, self.y)):
                raise ValueError("keyboard action cannot carry mouse fields")
            if kind is ActionKind.KEY_HOLD and self.hold_seconds <= 0:
                raise ValueError("KEY_HOLD requires a positive finite hold_seconds")
            if kind is ActionKind.KEY_TAP and self.hold_seconds != 0:
                raise ValueError("KEY_TAP cannot carry hold_seconds")
            return

        if self.key is not None:
            raise ValueError("mouse action cannot carry key")

        if kind is ActionKind.MOUSE_MOVE:
            self._require_coordinates()
            if self.button is not None or self.hold_seconds != 0:
                raise ValueError("MOUSE_MOVE cannot carry button or hold_seconds")
            return

        if kind in (ActionKind.MOUSE_CLICK, ActionKind.MOUSE_HOLD):
            self._require_coordinates()
            if not isinstance(self.button, str) or not self.button.strip():
                raise ValueError("mouse button action requires button")
            if kind is ActionKind.MOUSE_HOLD and self.hold_seconds <= 0:
                raise ValueError(
                    "MOUSE_HOLD requires a positive finite hold_seconds"
                )
            if kind is ActionKind.MOUSE_CLICK and self.hold_seconds != 0:
                raise ValueError("MOUSE_CLICK cannot carry hold_seconds")
            return

        raise ValueError("unsupported action kind")

    def _require_coordinates(self) -> None:
        if (
            isinstance(self.x, bool)
            or isinstance(self.y, bool)
            or not isinstance(self.x, int)
            or not isinstance(self.y, int)
        ):
            raise ValueError("mouse action requires integer client-relative x/y")


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    action_id: str
    status: ActionStatus
    started_monotonic: float
    completed_monotonic: float
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id.strip():
            raise ValueError("action_id must be a non-empty string")
        try:
            status = (
                self.status
                if isinstance(self.status, ActionStatus)
                else ActionStatus(self.status)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("unsupported action status") from error
        object.__setattr__(self, "status", status)

        for name, value in (
            ("started_monotonic", self.started_monotonic),
            ("completed_monotonic", self.completed_monotonic),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.completed_monotonic < self.started_monotonic:
            raise ValueError("completed_monotonic cannot precede started_monotonic")
        if self.failure_reason is not None and not self.failure_reason.strip():
            raise ValueError("failure_reason must not be empty")

    @property
    def succeeded(self) -> bool:
        return self.status is ActionStatus.SUCCEEDED
