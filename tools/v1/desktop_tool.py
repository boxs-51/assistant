from __future__ import annotations

import importlib
import math
import sys
import threading
import time
from copy import deepcopy
from typing import Any, Callable, Optional

from tools.v1._shared.contracts import failure_result, success_result, tool_result_schema
from tools.v1._shared.limits import (
    FloatLimitSpec,
    IntLimitSpec,
    resolve_float_limit,
    resolve_int_limit,
)


DESKTOP_TOOL_NAME = "desktop_automation"
DESKTOP_TOOL_VERSION = "2.0.0"

MAX_COORD_ABS = 1_000_000
MAX_SCREEN_DIMENSION = 1_000_000

MAX_TEXT_CHARS = 32_768
MAX_KEY_CHARS = 64
MAX_HOTKEY_KEYS = 16

CLICK_COUNT = IntLimitSpec("clicks", default=1, minimum=1, maximum=100)
PRESS_COUNT = IntLimitSpec("presses", default=1, minimum=1, maximum=100)

MAX_SCROLL_ABS = 10_000

MOVE_DURATION = FloatLimitSpec("duration", default=0.2, minimum=0.0, maximum=30.0)
DRAG_DURATION = FloatLimitSpec("duration", default=0.5, minimum=0.0, maximum=30.0)
TYPE_INTERVAL = FloatLimitSpec("interval", default=0.02, minimum=0.0, maximum=1.0)
PYAUTOGUI_PAUSE = FloatLimitSpec("pause", default=0.1, minimum=0.0, maximum=5.0)

CLIPBOARD_COPY_SETTLE_SECONDS = 0.05
CLIPBOARD_PASTE_SETTLE_SECONDS = 0.05

_MOUSE_BUTTONS = frozenset({"left", "right", "middle"})
_PYAUTOGUI_STATE_LOCK = threading.RLock()


_ACTION_SPECS: dict[str, dict[str, Any]] = {
    "get_screen_info": {
        "description": "Lấy kích thước màn hình và vị trí chuột hiện tại.",
        "base_risk": "LOW",
        "properties": {},
        "required": [],
    },
    "mouse_click": {
        "description": "Click chuột tại vị trí chỉ định hoặc vị trí hiện tại.",
        "base_risk": "HIGH",
        "properties": {
            "x": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "y": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "button": {"type": "string", "enum": sorted(_MOUSE_BUTTONS)},
            "clicks": {
                "type": "integer",
                "minimum": CLICK_COUNT.minimum,
                "maximum": CLICK_COUNT.maximum,
            },
        },
        "required": [],
    },
    "mouse_move": {
        "description": "Di chuyển chuột đến tọa độ đích.",
        "base_risk": "LOW",
        "properties": {
            "x": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "y": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "duration": {
                "type": "number",
                "minimum": MOVE_DURATION.minimum,
                "maximum": MOVE_DURATION.maximum,
            },
        },
        "required": ["x", "y"],
    },
    "mouse_drag": {
        "description": "Kéo chuột từ vị trí hiện tại hoặc điểm bắt đầu tường minh đến tọa độ đích.",
        "base_risk": "HIGH",
        "properties": {
            "x": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "y": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "start_x": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "start_y": {"type": "integer", "minimum": -MAX_COORD_ABS, "maximum": MAX_COORD_ABS},
            "button": {"type": "string", "enum": sorted(_MOUSE_BUTTONS)},
            "duration": {
                "type": "number",
                "minimum": DRAG_DURATION.minimum,
                "maximum": DRAG_DURATION.maximum,
            },
        },
        "required": ["x", "y"],
    },
    "mouse_scroll": {
        "description": "Cuộn chuột theo số nấc có dấu.",
        "base_risk": "LOW",
        "properties": {
            "clicks": {
                "type": "integer",
                "minimum": -MAX_SCROLL_ABS,
                "maximum": MAX_SCROLL_ABS,
                "not": {"const": 0},
            }
        },
        "required": ["clicks"],
    },
    "type_text": {
        "description": "Nhập văn bản vào ứng dụng đang focus; Unicode mặc định dùng Clipboard.",
        "base_risk": "HIGH",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS},
            "force_direct": {"type": "boolean"},
            "restore_clipboard": {"type": "boolean"},
            "interval": {
                "type": "number",
                "minimum": TYPE_INTERVAL.minimum,
                "maximum": TYPE_INTERVAL.maximum,
            },
        },
        "required": ["text"],
    },
    "press_key": {
        "description": "Nhấn một phím đơn một số lần hữu hạn.",
        "base_risk": "HIGH",
        "properties": {
            "key": {"type": "string", "minLength": 1, "maxLength": MAX_KEY_CHARS},
            "presses": {
                "type": "integer",
                "minimum": PRESS_COUNT.minimum,
                "maximum": PRESS_COUNT.maximum,
            },
        },
        "required": ["key"],
    },
    "hotkey": {
        "description": "Thực thi một tổ hợp phím hữu hạn.",
        "base_risk": "HIGH",
        "properties": {
            "keys": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_HOTKEY_KEYS,
                "items": {"type": "string", "minLength": 1, "maxLength": MAX_KEY_CHARS},
            }
        },
        "required": ["keys"],
    },
}


def _build_actions_metadata() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for action, spec in _ACTION_SPECS.items():
        result[action] = {
            "name": action,
            "description": spec["description"],
            "base_risk": spec["base_risk"],
            "danger_patterns": [],
            "parameters": {
                "type": "object",
                "properties": deepcopy(spec["properties"]),
                "required": list(spec["required"]),
            },
        }
    return result


ACTIONS_METADATA = _build_actions_metadata()

_ROOT_PROPERTIES: dict[str, Any] = {
    "action": {"type": "string", "enum": list(_ACTION_SPECS)},
}
for _spec in _ACTION_SPECS.values():
    for _name, _schema in _spec["properties"].items():
        _ROOT_PROPERTIES.setdefault(_name, deepcopy(_schema))

# V1 root metadata is a broad physical dispatcher schema. "clicks" is shared
# by positive mouse-click counts and signed scroll deltas, while per-action
# ACTIONS_METADATA retains the stricter action-specific contracts.
_ROOT_PROPERTIES["clicks"] = {
    "type": "integer",
    "minimum": -MAX_SCROLL_ABS,
    "maximum": MAX_SCROLL_ABS,
}

def _desktop_input_schema(action: str) -> dict[str, Any]:
    spec = _ACTION_SPECS[action]
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": deepcopy(spec["properties"]),
        "required": list(spec["required"]),
    }
    if action == "mouse_click":
        schema["allOf"] = [
            {
                "if": {"required": ["x"]},
                "then": {"required": ["y"]},
            },
            {
                "if": {"required": ["y"]},
                "then": {"required": ["x"]},
            },
        ]
    elif action == "mouse_drag":
        schema["allOf"] = [
            {
                "if": {"required": ["start_x"]},
                "then": {"required": ["start_y"]},
            },
            {
                "if": {"required": ["start_y"]},
                "then": {"required": ["start_x"]},
            },
        ]
    return schema


_DESKTOP_LOGICAL_EXPORTS = (
    ("desktop.screen_info", "get_screen_info", "IDEMPOTENT", ["READ"]),
    ("desktop.mouse_move", "mouse_move", "UNKNOWN", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.mouse_click", "mouse_click", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.mouse_drag", "mouse_drag", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.mouse_scroll", "mouse_scroll", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.type_text", "type_text", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.press_key", "press_key", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
    ("desktop.hotkey", "hotkey", "NON_IDEMPOTENT", ["EXTERNAL_SIDE_EFFECT"]),
)


def _build_logical_exports() -> list[dict[str, Any]]:
    exports: list[dict[str, Any]] = []
    for capability_id, action, idempotency, effects in _DESKTOP_LOGICAL_EXPORTS:
        spec = _ACTION_SPECS[action]
        exports.append(
            {
                "id": capability_id,
                "version": "1.0",
                "name": capability_id,
                "description": spec["description"],
                "bind": {"action": action},
                "input_schema": _desktop_input_schema(action),
                "output_schema": tool_result_schema({}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": idempotency,
                "effects": effects,
                "base_risk": spec["base_risk"],
                "required_scopes": [],
                "required_permissions": [],
                "danger_patterns": [],
            }
        )
    return exports


TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": DESKTOP_TOOL_NAME,
    "version": DESKTOP_TOOL_VERSION,
    "description": (
        "Điều khiển chuột/bàn phím desktop với lazy dependencies, hard bounds, "
        "scoped PyAutoGUI state và kết quả ToolResult không echo nội dung được gõ."
    ),
    "expose_root": False,
    "exports": _build_logical_exports(),
    # Legacy root descriptive fields remain only for direct physical callers.
    "base_risk": "HIGH",
    "effects": ["EXECUTE", "EXTERNAL_SIDE_EFFECT"],
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": _ROOT_PROPERTIES,
        "required": ["action"],
    },
}

class _DesktopToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _as_invalid_argument(exc: Exception) -> _DesktopToolError:
    return _DesktopToolError(
        "INVALID_ARGUMENT",
        str(exc),
        {"exception_type": type(exc).__name__},
    )


def _resolve_int(value: Optional[int], spec: IntLimitSpec) -> int:
    try:
        return resolve_int_limit(value, spec)
    except Exception as exc:
        if exc.__class__.__module__.startswith("tools.v1._shared"):
            raise _as_invalid_argument(exc) from exc
        raise


def _resolve_float(
    value: Optional[float | int],
    spec: FloatLimitSpec,
) -> float:
    try:
        return resolve_float_limit(value, spec)
    except Exception as exc:
        if exc.__class__.__module__.startswith("tools.v1._shared"):
            raise _as_invalid_argument(exc) from exc
        raise


def _validate_bool(value: Any, *, name: str) -> bool:
    if type(value) is not bool:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} must be a boolean",
        )
    return value


def _validate_coordinate(value: Any, *, name: str) -> int:
    if type(value) is not int:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} must be an integer",
        )
    if value < -MAX_COORD_ABS or value > MAX_COORD_ABS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} exceeds the coordinate hard limit",
        )
    return value


def _validate_optional_coordinate(value: Any, *, name: str) -> Optional[int]:
    if value is None:
        return None
    return _validate_coordinate(value, name=name)


def _validate_coordinate_pair(
    x: Any,
    y: Any,
    *,
    x_name: str,
    y_name: str,
    required: bool,
) -> tuple[Optional[int], Optional[int]]:
    if x is None and y is None:
        if required:
            raise _DesktopToolError(
                "INVALID_ARGUMENT",
                f"{x_name} and {y_name} are required",
            )
        return None, None
    if x is None or y is None:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{x_name} and {y_name} must be provided together",
        )
    return (
        _validate_coordinate(x, name=x_name),
        _validate_coordinate(y, name=y_name),
    )


def _validate_button(button: Any) -> str:
    if not isinstance(button, str):
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "button must be a string",
        )
    normalized = button.strip().lower()
    if normalized not in _MOUSE_BUTTONS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "button must be left, right, or middle",
        )
    return normalized


def _validate_scroll(clicks: Any) -> int:
    if type(clicks) is not int:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "clicks must be an integer",
        )
    if clicks == 0 or abs(clicks) > MAX_SCROLL_ABS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"scroll clicks must be nonzero with absolute value <= {MAX_SCROLL_ABS}",
        )
    return clicks


def _validate_key(key: Any, *, name: str = "key") -> str:
    if not isinstance(key, str):
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} must be a string",
        )
    normalized = key.strip().lower()
    if not normalized:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} must be non-empty",
        )
    if len(normalized) > MAX_KEY_CHARS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"{name} exceeds the hard key-length limit",
        )
    return normalized


def _validate_hotkey(keys: Any) -> list[str]:
    if not isinstance(keys, list):
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "keys must be a list",
        )
    if not keys or len(keys) > MAX_HOTKEY_KEYS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"keys must contain between 1 and {MAX_HOTKEY_KEYS} entries",
        )
    return [_validate_key(value, name=f"keys[{index}]") for index, value in enumerate(keys)]


def _validate_text(text: Any) -> str:
    if not isinstance(text, str):
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "text must be a string",
        )
    if not text:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "text must be non-empty",
        )
    if "\x00" in text:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            "text contains a NUL character",
        )
    if len(text) > MAX_TEXT_CHARS:
        raise _DesktopToolError(
            "INVALID_ARGUMENT",
            f"text exceeds the hard limit of {MAX_TEXT_CHARS} characters",
        )
    return text


class DesktopAutomation:
    """Bounded, lazy desktop automation with scoped backend state."""

    def __init__(
        self,
        failsafe: bool = True,
        pause: float = PYAUTOGUI_PAUSE.default,
    ) -> None:
        self.failsafe = _validate_bool(failsafe, name="failsafe")
        self.pause = _resolve_float(pause, PYAUTOGUI_PAUSE)
        self._pyautogui: Any = None
        self._pyperclip: Any = None
        self._unicode_keyboard: Any = None

    def _success(
        self,
        action: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return success_result(
            tool=DESKTOP_TOOL_NAME,
            action=action,
            version=DESKTOP_TOOL_VERSION,
            data=data,
        )

    def _failure(
        self,
        action: str,
        error: _DesktopToolError,
    ) -> dict[str, Any]:
        return failure_result(
            tool=DESKTOP_TOOL_NAME,
            action=action,
            version=DESKTOP_TOOL_VERSION,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    @staticmethod
    def _load_dependency(module_name: str, dependency_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            raise _DesktopToolError(
                "DEPENDENCY_UNAVAILABLE",
                f"{dependency_name} is unavailable in the current environment",
                {
                    "dependency": dependency_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc

    def _load_pyautogui(self) -> Any:
        if self._pyautogui is None:
            self._pyautogui = self._load_dependency("pyautogui", "pyautogui")
        return self._pyautogui

    def _load_clipboard(self) -> Any:
        if self._pyperclip is None:
            self._pyperclip = self._load_dependency("pyperclip", "pyperclip")
        return self._pyperclip

    def _load_unicode_keyboard(self) -> Any:
        if self._unicode_keyboard is not None:
            return self._unicode_keyboard
        module = self._load_dependency("pynput.keyboard", "pynput.keyboard")
        try:
            controller_class = module.Controller
            controller = controller_class()
        except Exception as exc:
            raise _DesktopToolError(
                "DEPENDENCY_UNAVAILABLE",
                "pynput.keyboard Controller is unavailable in the current environment",
                {
                    "dependency": "pynput.keyboard",
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        self._unicode_keyboard = controller
        return controller

    @staticmethod
    def _is_failsafe_exception(backend: Any, exc: Exception) -> bool:
        fail_safe_type = getattr(backend, "FailSafeException", None)
        return isinstance(fail_safe_type, type) and isinstance(exc, fail_safe_type)

    def _call_pyautogui(
        self,
        backend: Any,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            method = getattr(backend, method_name)
            return method(*args, **kwargs)
        except Exception as exc:
            if self._is_failsafe_exception(backend, exc):
                raise _DesktopToolError(
                    "DESKTOP_FAILSAFE_TRIGGERED",
                    "PyAutoGUI fail-safe was triggered",
                    {"operation": method_name},
                ) from exc
            raise _DesktopToolError(
                "DESKTOP_OPERATION_FAILED",
                "desktop backend operation failed",
                {
                    "operation": method_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc

    def _run_with_scoped_pyautogui(
        self,
        callback: Callable[[Any], Any],
    ) -> Any:
        backend = self._load_pyautogui()
        with _PYAUTOGUI_STATE_LOCK:
            try:
                old_failsafe = backend.FAILSAFE
                old_pause = backend.PAUSE
            except Exception as exc:
                raise _DesktopToolError(
                    "DESKTOP_BACKEND_CONFIG_FAILED",
                    "PyAutoGUI global state could not be read",
                    {
                        "phase": "snapshot",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc

            try:
                backend.FAILSAFE = self.failsafe
                backend.PAUSE = self.pause
            except Exception as exc:
                try:
                    backend.FAILSAFE = old_failsafe
                    backend.PAUSE = old_pause
                except Exception as restore_exc:
                    raise _DesktopToolError(
                        "DESKTOP_BACKEND_STATE_RESTORE_FAILED",
                        "PyAutoGUI global state could not be restored",
                        {
                            "phase": "apply_rollback",
                            "exception_type": type(restore_exc).__name__,
                        },
                    ) from restore_exc
                raise _DesktopToolError(
                    "DESKTOP_BACKEND_CONFIG_FAILED",
                    "PyAutoGUI global state could not be applied",
                    {
                        "phase": "apply",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc

            original: BaseException | None = None
            result: Any = None
            try:
                result = callback(backend)
            except BaseException as exc:
                original = exc

            restore_error: Exception | None = None
            try:
                backend.FAILSAFE = old_failsafe
                backend.PAUSE = old_pause
            except Exception as exc:
                restore_error = exc

            if original is not None:
                if restore_error is not None and isinstance(
                    original,
                    _DesktopToolError,
                ):
                    raise _DesktopToolError(
                        "DESKTOP_BACKEND_STATE_RESTORE_FAILED",
                        "PyAutoGUI global state could not be restored",
                        {
                            "phase": "restore_after_error",
                            "exception_type": type(restore_error).__name__,
                        },
                    ) from restore_error
                raise original

            if restore_error is not None:
                raise _DesktopToolError(
                    "DESKTOP_BACKEND_STATE_RESTORE_FAILED",
                    "PyAutoGUI global state could not be restored",
                    {
                        "phase": "restore_after_success",
                        "exception_type": type(restore_error).__name__,
                    },
                ) from restore_error
            return result

    @staticmethod
    def _validate_screen_dimension(value: Any, *, name: str) -> int:
        if type(value) is not int or value <= 0 or value > MAX_SCREEN_DIMENSION:
            raise _DesktopToolError(
                "DESKTOP_PROPERTY_FAILED",
                f"{name} returned an invalid screen dimension",
                {"property": name},
            )
        return value

    def get_screen_info(self) -> dict[str, Any]:
        action = "get_screen_info"
        try:
            def operation(backend: Any) -> dict[str, int]:
                size = self._call_pyautogui(backend, "size")
                position = self._call_pyautogui(backend, "position")
                try:
                    width, height = size
                    mouse_x, mouse_y = position
                except Exception as exc:
                    raise _DesktopToolError(
                        "DESKTOP_PROPERTY_FAILED",
                        "desktop backend returned malformed screen information",
                        {"exception_type": type(exc).__name__},
                    ) from exc
                screen_width = self._validate_screen_dimension(
                    width, name="screen_width"
                )
                screen_height = self._validate_screen_dimension(
                    height, name="screen_height"
                )
                if (
                    type(mouse_x) is not int
                    or type(mouse_y) is not int
                    or abs(mouse_x) > MAX_COORD_ABS
                    or abs(mouse_y) > MAX_COORD_ABS
                ):
                    raise _DesktopToolError(
                        "DESKTOP_PROPERTY_FAILED",
                        "desktop backend returned invalid mouse coordinates",
                        {"property": "mouse_position"},
                    )
                return {
                    "screen_width": screen_width,
                    "screen_height": screen_height,
                    "mouse_x": mouse_x,
                    "mouse_y": mouse_y,
                }

            data = self._run_with_scoped_pyautogui(operation)
            return self._success(action, data)
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def mouse_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = CLICK_COUNT.default,
    ) -> dict[str, Any]:
        action = "mouse_click"
        try:
            validated_x, validated_y = _validate_coordinate_pair(
                x,
                y,
                x_name="x",
                y_name="y",
                required=False,
            )
            validated_button = _validate_button(button)
            validated_clicks = _resolve_int(clicks, CLICK_COUNT)

            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "click",
                    x=validated_x,
                    y=validated_y,
                    clicks=validated_clicks,
                    button=validated_button,
                )
            )
            return self._success(
                action,
                {
                    "x": validated_x,
                    "y": validated_y,
                    "position_mode": (
                        "current" if validated_x is None else "explicit"
                    ),
                    "button": validated_button,
                    "clicks": validated_clicks,
                },
            )
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def mouse_move(
        self,
        x: int,
        y: int,
        duration: float = MOVE_DURATION.default,
    ) -> dict[str, Any]:
        action = "mouse_move"
        try:
            validated_x, validated_y = _validate_coordinate_pair(
                x,
                y,
                x_name="x",
                y_name="y",
                required=True,
            )
            assert validated_x is not None and validated_y is not None
            validated_duration = _resolve_float(duration, MOVE_DURATION)
            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "moveTo",
                    validated_x,
                    validated_y,
                    duration=validated_duration,
                )
            )
            return self._success(
                action,
                {
                    "x": validated_x,
                    "y": validated_y,
                    "duration_seconds": validated_duration,
                },
            )
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def mouse_drag(
        self,
        x: int,
        y: int,
        start_x: Optional[int] = None,
        start_y: Optional[int] = None,
        button: str = "left",
        duration: float = DRAG_DURATION.default,
    ) -> dict[str, Any]:
        action = "mouse_drag"
        try:
            target_x, target_y = _validate_coordinate_pair(
                x,
                y,
                x_name="x",
                y_name="y",
                required=True,
            )
            assert target_x is not None and target_y is not None
            validated_start_x, validated_start_y = _validate_coordinate_pair(
                start_x,
                start_y,
                x_name="start_x",
                y_name="start_y",
                required=False,
            )
            validated_button = _validate_button(button)
            validated_duration = _resolve_float(duration, DRAG_DURATION)

            def operation(backend: Any) -> None:
                if validated_start_x is not None:
                    self._call_pyautogui(
                        backend,
                        "moveTo",
                        validated_start_x,
                        validated_start_y,
                    )
                self._call_pyautogui(
                    backend,
                    "dragTo",
                    target_x,
                    target_y,
                    duration=validated_duration,
                    button=validated_button,
                )

            self._run_with_scoped_pyautogui(operation)
            return self._success(
                action,
                {
                    "x": target_x,
                    "y": target_y,
                    "start_x": validated_start_x,
                    "start_y": validated_start_y,
                    "explicit_start": validated_start_x is not None,
                    "button": validated_button,
                    "duration_seconds": validated_duration,
                },
            )
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def mouse_scroll(self, clicks: int) -> dict[str, Any]:
        action = "mouse_scroll"
        try:
            validated_clicks = _validate_scroll(clicks)
            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "scroll",
                    validated_clicks,
                )
            )
            return self._success(action, {"clicks": validated_clicks})
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def press_key(
        self,
        key: str,
        presses: int = PRESS_COUNT.default,
    ) -> dict[str, Any]:
        action = "press_key"
        try:
            validated_key = _validate_key(key)
            validated_presses = _resolve_int(presses, PRESS_COUNT)
            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "press",
                    validated_key,
                    presses=validated_presses,
                )
            )
            return self._success(
                action,
                {
                    "key": validated_key,
                    "presses": validated_presses,
                },
            )
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        action = "hotkey"
        try:
            validated_keys = _validate_hotkey(keys)
            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "hotkey",
                    *validated_keys,
                )
            )
            return self._success(
                action,
                {
                    "keys": validated_keys,
                    "key_count": len(validated_keys),
                },
            )
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    @staticmethod
    def _clipboard_call(
        clipboard: Any,
        method_name: str,
        *args: Any,
    ) -> Any:
        try:
            method = getattr(clipboard, method_name)
            return method(*args)
        except Exception as exc:
            raise _DesktopToolError(
                "DESKTOP_CLIPBOARD_ERROR",
                "clipboard operation failed",
                {
                    "operation": method_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc

    @staticmethod
    def _paste_keys() -> tuple[str, str]:
        return ("command", "v") if sys.platform == "darwin" else ("ctrl", "v")

    def _type_ascii(
        self,
        text: str,
        interval: float,
    ) -> dict[str, Any]:
        self._run_with_scoped_pyautogui(
            lambda backend: self._call_pyautogui(
                backend,
                "write",
                text,
                interval=interval,
            )
        )
        return {
            "character_count": len(text),
            "method": "pyautogui",
            "clipboard_restored": None,
        }

    def _type_unicode_direct(self, text: str) -> dict[str, Any]:
        keyboard = self._load_unicode_keyboard()
        try:
            keyboard.type(text)
        except Exception as exc:
            raise _DesktopToolError(
                "DESKTOP_OPERATION_FAILED",
                "direct Unicode keyboard input failed",
                {
                    "operation": "pynput.type",
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        return {
            "character_count": len(text),
            "method": "pynput",
            "clipboard_restored": None,
        }

    def _type_unicode_clipboard(
        self,
        text: str,
        *,
        restore_clipboard: bool,
    ) -> dict[str, Any]:
        clipboard = self._load_clipboard()
        old_clipboard: Any = None
        clipboard_mutated = False
        original_error: _DesktopToolError | None = None
        passthrough_error: BaseException | None = None

        if restore_clipboard:
            old_clipboard = self._clipboard_call(clipboard, "paste")
            if not isinstance(old_clipboard, str):
                raise _DesktopToolError(
                    "DESKTOP_CLIPBOARD_ERROR",
                    "clipboard snapshot is not text",
                    {"operation": "paste"},
                )

        try:
            self._clipboard_call(clipboard, "copy", text)
            clipboard_mutated = True
            time.sleep(CLIPBOARD_COPY_SETTLE_SECONDS)

            paste_keys = self._paste_keys()
            self._run_with_scoped_pyautogui(
                lambda backend: self._call_pyautogui(
                    backend,
                    "hotkey",
                    *paste_keys,
                )
            )
            time.sleep(CLIPBOARD_PASTE_SETTLE_SECONDS)
        except _DesktopToolError as exc:
            original_error = exc
        except BaseException as exc:
            passthrough_error = exc
        finally:
            if restore_clipboard and clipboard_mutated:
                try:
                    self._clipboard_call(clipboard, "copy", old_clipboard)
                except _DesktopToolError as restore_exc:
                    if passthrough_error is None:
                        raise _DesktopToolError(
                            "DESKTOP_CLIPBOARD_RESTORE_FAILED",
                            "clipboard could not be restored after text input",
                            {
                                "trigger": (
                                    original_error.code
                                    if original_error is not None
                                    else "success"
                                ),
                                "exception_type": restore_exc.details.get(
                                    "exception_type",
                                    type(restore_exc).__name__,
                                ),
                            },
                        ) from restore_exc

        if passthrough_error is not None:
            raise passthrough_error
        if original_error is not None:
            raise original_error

        return {
            "character_count": len(text),
            "method": "clipboard",
            "clipboard_restored": restore_clipboard,
        }

    def type_text(
        self,
        text: str,
        force_direct: bool = False,
        restore_clipboard: bool = True,
        interval: float = TYPE_INTERVAL.default,
    ) -> dict[str, Any]:
        action = "type_text"
        try:
            validated_text = _validate_text(text)
            validated_force_direct = _validate_bool(
                force_direct,
                name="force_direct",
            )
            validated_restore = _validate_bool(
                restore_clipboard,
                name="restore_clipboard",
            )
            validated_interval = _resolve_float(interval, TYPE_INTERVAL)

            is_unicode = any(ord(char) > 127 for char in validated_text)
            if not is_unicode:
                data = self._type_ascii(validated_text, validated_interval)
            elif validated_force_direct:
                data = self._type_unicode_direct(validated_text)
            else:
                data = self._type_unicode_clipboard(
                    validated_text,
                    restore_clipboard=validated_restore,
                )
            return self._success(action, data)
        except _DesktopToolError as exc:
            return self._failure(action, exc)

    def execute(
        self,
        action: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        start_x: Optional[int] = None,
        start_y: Optional[int] = None,
        button: str = "left",
        clicks: Optional[int] = None,
        duration: Optional[float] = None,
        text: Optional[str] = None,
        force_direct: bool = False,
        restore_clipboard: bool = True,
        interval: float = TYPE_INTERVAL.default,
        key: Optional[str] = None,
        presses: int = PRESS_COUNT.default,
        keys: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        aliases = {
            "screen_info": "get_screen_info",
            "info": "get_screen_info",
            "click": "mouse_click",
            "move": "mouse_move",
            "drag": "mouse_drag",
            "scroll": "mouse_scroll",
            "type": "type_text",
            "write": "type_text",
            "press": "press_key",
            "shortcut": "hotkey",
        }
        canonical = aliases.get(action, action)

        if canonical == "get_screen_info":
            return self.get_screen_info()
        if canonical == "mouse_click":
            return self.mouse_click(
                x=x,
                y=y,
                button=button,
                clicks=CLICK_COUNT.default if clicks is None else clicks,
            )
        if canonical == "mouse_move":
            if x is None or y is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "x and y are required for mouse_move",
                    ),
                )
            return self.mouse_move(
                x=x,
                y=y,
                duration=MOVE_DURATION.default if duration is None else duration,
            )
        if canonical == "mouse_drag":
            if x is None or y is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "x and y are required for mouse_drag",
                    ),
                )
            return self.mouse_drag(
                x=x,
                y=y,
                start_x=start_x,
                start_y=start_y,
                button=button,
                duration=DRAG_DURATION.default if duration is None else duration,
            )
        if canonical == "mouse_scroll":
            if clicks is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "clicks is required for mouse_scroll",
                    ),
                )
            return self.mouse_scroll(clicks=clicks)
        if canonical == "type_text":
            if text is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "text is required for type_text",
                    ),
                )
            return self.type_text(
                text=text,
                force_direct=force_direct,
                restore_clipboard=restore_clipboard,
                interval=interval,
            )
        if canonical == "press_key":
            if key is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "key is required for press_key",
                    ),
                )
            return self.press_key(key=key, presses=presses)
        if canonical == "hotkey":
            if keys is None:
                return self._failure(
                    canonical,
                    _DesktopToolError(
                        "INVALID_ARGUMENT",
                        "keys are required for hotkey",
                    ),
                )
            return self.hotkey(keys=keys)

        return failure_result(
            tool=DESKTOP_TOOL_NAME,
            action=action if isinstance(action, str) and action else "unknown",
            version=DESKTOP_TOOL_VERSION,
            code="INVALID_ARGUMENT",
            message="unsupported desktop action",
            details={},
        )


_default_desktop_automation = DesktopAutomation()


def get_screen_info() -> dict[str, Any]:
    return _default_desktop_automation.get_screen_info()


def mouse_click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    button: str = "left",
    clicks: int = CLICK_COUNT.default,
) -> dict[str, Any]:
    return _default_desktop_automation.mouse_click(
        x=x,
        y=y,
        button=button,
        clicks=clicks,
    )


def mouse_move(
    x: int,
    y: int,
    duration: float = MOVE_DURATION.default,
) -> dict[str, Any]:
    return _default_desktop_automation.mouse_move(
        x=x,
        y=y,
        duration=duration,
    )


def mouse_drag(
    x: int,
    y: int,
    start_x: Optional[int] = None,
    start_y: Optional[int] = None,
    button: str = "left",
    duration: float = DRAG_DURATION.default,
) -> dict[str, Any]:
    return _default_desktop_automation.mouse_drag(
        x=x,
        y=y,
        start_x=start_x,
        start_y=start_y,
        button=button,
        duration=duration,
    )


def mouse_scroll(clicks: int) -> dict[str, Any]:
    return _default_desktop_automation.mouse_scroll(clicks=clicks)


def type_text(
    text: str,
    force_direct: bool = False,
    restore_clipboard: bool = True,
    interval: float = TYPE_INTERVAL.default,
) -> dict[str, Any]:
    return _default_desktop_automation.type_text(
        text=text,
        force_direct=force_direct,
        restore_clipboard=restore_clipboard,
        interval=interval,
    )


def press_key(
    key: str,
    presses: int = PRESS_COUNT.default,
) -> dict[str, Any]:
    return _default_desktop_automation.press_key(
        key=key,
        presses=presses,
    )


def hotkey(keys: list[str]) -> dict[str, Any]:
    return _default_desktop_automation.hotkey(keys=keys)


def run(action: str, **kwargs: Any) -> dict[str, Any]:
    return _default_desktop_automation.execute(action=action, **kwargs)
