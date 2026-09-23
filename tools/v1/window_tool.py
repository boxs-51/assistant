from __future__ import annotations

import importlib
import time
from typing import Any, Optional

from tools.v1._shared.contracts import failure_result, success_result, tool_result_schema
from tools.v1._shared.limits import IntLimitSpec, resolve_int_limit


WINDOW_TOOL_VERSION = "2.0.0"

MAX_TITLE_QUERY_CHARS = 512
MAX_WINDOW_TITLE_CHARS = 4_096
MAX_APP_NAME_CHARS = 1_024

MAX_WINDOWS_ENUMERATED = 2_000
MAX_AMBIGUITY_CANDIDATES = 10

WINDOW_RESULTS = IntLimitSpec(
    "max_results",
    default=100,
    minimum=1,
    maximum=500,
)

MAX_WINDOW_HANDLE = (1 << 64) - 1
MAX_PID = (1 << 31) - 1

CLOSE_VERIFY_TIMEOUT_SECONDS = 2.0
CLOSE_VERIFY_POLL_SECONDS = 0.05


_WINDOW_SELECTOR_PROPERTIES = {
    "title_query": {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_TITLE_QUERY_CHARS,
    },
    "window_handle": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_WINDOW_HANDLE,
    },
    "pid": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_PID,
    },
}


def _single_target_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(_WINDOW_SELECTOR_PROPERTIES),
        "required": [],
        "anyOf": [
            {"required": ["title_query"]},
            {"required": ["window_handle"]},
            {"required": ["pid"]},
        ],
    }


TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "window_tool",
    "version": WINDOW_TOOL_VERSION,
    "description": (
        "Quản lý cửa sổ ứng dụng với targeting an toàn theo title/handle/PID. "
        "Các side effect chỉ chạy khi selector resolve đúng một cửa sổ; "
        "kết quả trả về theo ToolResult có cấu trúc."
    ),
    "expose_root": False,
    "exports": [
        {
            "id": "window.list",
            "version": "1.0",
            "name": "window.list",
            "description": "Liệt kê cửa sổ ứng dụng với số kết quả bị giới hạn.",
            "bind": {"action": "list"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "minimum": WINDOW_RESULTS.minimum,
                        "maximum": WINDOW_RESULTS.maximum,
                    }
                },
                "required": [],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.find",
            "version": "1.0",
            "name": "window.find",
            "description": "Tìm cửa sổ theo title_query không phân biệt hoa thường.",
            "bind": {"action": "find"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title_query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_TITLE_QUERY_CHARS,
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": WINDOW_RESULTS.minimum,
                        "maximum": WINDOW_RESULTS.maximum,
                    },
                },
                "required": ["title_query"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.geometry",
            "version": "1.0",
            "name": "window.geometry",
            "description": "Đọc geometry của đúng một cửa sổ được resolve bằng selector.",
            "bind": {"action": "get_geometry"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.focus",
            "version": "1.0",
            "name": "window.focus",
            "description": "Focus đúng một cửa sổ sau khi selector resolve duy nhất.",
            "bind": {"action": "focus"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.close",
            "version": "1.0",
            "name": "window.close",
            "description": "Đóng đúng một cửa sổ sau khi selector resolve duy nhất.",
            "bind": {"action": "close"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "NON_IDEMPOTENT",
            "effects": ["EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.minimize",
            "version": "1.0",
            "name": "window.minimize",
            "description": "Minimize đúng một cửa sổ sau khi selector resolve duy nhất.",
            "bind": {"action": "minimize"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.maximize",
            "version": "1.0",
            "name": "window.maximize",
            "description": "Maximize đúng một cửa sổ sau khi selector resolve duy nhất.",
            "bind": {"action": "maximize"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "window.restore",
            "version": "1.0",
            "name": "window.restore",
            "description": "Restore đúng một cửa sổ sau khi selector resolve duy nhất.",
            "bind": {"action": "restore"},
            "input_schema": _single_target_schema(),
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
    ],
    # Legacy root descriptive fields remain only for direct physical callers.
    "base_risk": "MEDIUM",
    "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "find",
                    "get_geometry",
                    "focus",
                    "close",
                    "minimize",
                    "maximize",
                    "restore",
                ],
            },
            "title_query": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_TITLE_QUERY_CHARS,
            },
            "window_handle": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_WINDOW_HANDLE,
            },
            "pid": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_PID,
            },
            "max_results": {
                "type": "integer",
                "minimum": WINDOW_RESULTS.minimum,
                "maximum": WINDOW_RESULTS.maximum,
            },
        },
        "required": ["action"],
    },
}

class _WindowToolError(Exception):
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


def _bounded_text(value: str, maximum: int) -> tuple[str, bool]:
    if len(value) <= maximum:
        return value, False
    return value[:maximum], True


def _validate_title_query(
    title_query: Optional[str],
    *,
    required: bool,
) -> Optional[str]:
    if title_query is None:
        if required:
            raise _WindowToolError(
                "INVALID_ARGUMENT",
                "title_query is required for this window action",
            )
        return None
    if not isinstance(title_query, str) or not title_query.strip():
        raise _WindowToolError(
            "INVALID_ARGUMENT",
            "title_query must be a non-empty string",
        )
    if len(title_query) > MAX_TITLE_QUERY_CHARS:
        raise _WindowToolError(
            "INVALID_ARGUMENT",
            f"title_query exceeds the hard limit of {MAX_TITLE_QUERY_CHARS} characters",
        )
    return title_query.strip()


def _validate_optional_int(
    value: Any,
    *,
    name: str,
    maximum: int,
) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int:
        raise _WindowToolError(
            "INVALID_ARGUMENT",
            f"{name} must be an integer",
        )
    if value < 1 or value > maximum:
        raise _WindowToolError(
            "INVALID_ARGUMENT",
            f"{name} must be within [1, {maximum}]",
        )
    return value


class WindowTool:
    """Bounded, ambiguity-safe window enumeration and control."""

    def __init__(self) -> None:
        self._backend: Any = None

    def _success(
        self,
        action: str,
        data: Any,
        *,
        truncated: bool = False,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return success_result(
            tool="window_tool",
            action=action,
            version=WINDOW_TOOL_VERSION,
            data=data,
            truncated=truncated,
            warnings=warnings,
        )

    def _failure(
        self,
        action: str,
        error: _WindowToolError,
    ) -> dict[str, Any]:
        return failure_result(
            tool="window_tool",
            action=action,
            version=WINDOW_TOOL_VERSION,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    def _load_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        try:
            backend = importlib.import_module("pywinctl")
        except Exception as exc:
            raise _WindowToolError(
                "DEPENDENCY_UNAVAILABLE",
                "window backend is unavailable in the current environment",
                {"exception_type": type(exc).__name__},
            ) from exc
        self._backend = backend
        return backend

    def _bounded_windows(self, values: Any) -> list[Any]:
        windows: list[Any] = []
        try:
            iterator = iter(values)
            for index, window in enumerate(iterator):
                if index >= MAX_WINDOWS_ENUMERATED:
                    raise _WindowToolError(
                        "WINDOW_ENUMERATION_LIMIT",
                        "window enumeration exceeded the hard result budget",
                        {"max_windows_enumerated": MAX_WINDOWS_ENUMERATED},
                    )
                windows.append(window)
        except _WindowToolError:
            raise
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_ENUMERATION_FAILED",
                "window enumeration could not be consumed",
                {"exception_type": type(exc).__name__},
            ) from exc
        return windows

    def _enumerate_all(self) -> list[Any]:
        backend = self._load_backend()
        try:
            values = backend.getAllWindows()
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_ENUMERATION_FAILED",
                "window backend enumeration failed",
                {"exception_type": type(exc).__name__},
            ) from exc
        return self._bounded_windows(values)

    def _find_by_title(self, title_query: str) -> list[Any]:
        backend = self._load_backend()
        try:
            values = backend.getWindowsWithTitle(
                title_query,
                condition=backend.Re.CONTAINS,
                flags=backend.Re.IGNORECASE,
            )
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_ENUMERATION_FAILED",
                "window backend title search failed",
                {"exception_type": type(exc).__name__},
            ) from exc
        return self._bounded_windows(values)

    def _window_title(self, window: Any) -> str:
        try:
            value = window.title
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window title could not be read",
                {"property": "title", "exception_type": type(exc).__name__},
            ) from exc
        if value is None:
            return ""
        if not isinstance(value, str):
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window title has an invalid type",
                {"property": "title"},
            )
        return value

    def _window_handle(self, window: Any) -> int:
        try:
            value = window.getHandle()
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window handle could not be read",
                {"property": "window_handle", "exception_type": type(exc).__name__},
            ) from exc
        if type(value) is not int or value < 1 or value > MAX_WINDOW_HANDLE:
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window handle is invalid",
                {"property": "window_handle"},
            )
        return value

    def _window_pid(self, window: Any) -> Optional[int]:
        try:
            value = window.getPID()
        except Exception:
            return None
        if type(value) is int and 1 <= value <= MAX_PID:
            return value
        return None

    def _window_app_name(self, window: Any) -> Optional[str]:
        try:
            value = window.getAppName()
        except Exception:
            return None
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        return value

    def _descriptor(self, window: Any) -> dict[str, Any]:
        handle = self._window_handle(window)
        pid = self._window_pid(window)
        title_raw = self._window_title(window)
        app_raw = self._window_app_name(window)

        title, title_truncated = _bounded_text(
            title_raw,
            MAX_WINDOW_TITLE_CHARS,
        )
        if app_raw is None:
            app_name = None
            app_name_truncated = False
        else:
            app_name, app_name_truncated = _bounded_text(
                app_raw,
                MAX_APP_NAME_CHARS,
            )

        return {
            "selector": {
                "window_handle": handle,
                "pid": pid,
            },
            "title": title,
            "title_truncated": title_truncated,
            "app_name": app_name,
            "app_name_truncated": app_name_truncated,
        }

    @staticmethod
    def _descriptor_sort_key(
        descriptor: dict[str, Any],
    ) -> tuple[str, int, int]:
        selector = descriptor["selector"]
        return (
            descriptor["title"].casefold(),
            selector["pid"] if selector["pid"] is not None else -1,
            selector["window_handle"],
        )

    def _descriptors_sorted(
        self,
        windows: list[Any],
    ) -> list[dict[str, Any]]:
        descriptors = [self._descriptor(window) for window in windows]
        descriptors.sort(key=self._descriptor_sort_key)
        return descriptors

    def _target_candidates(
        self,
        *,
        title_query: Optional[str],
        window_handle: Optional[int],
        pid: Optional[int],
    ) -> list[Any]:
        if window_handle is not None or pid is not None:
            candidates = self._enumerate_all()
        else:
            assert title_query is not None
            candidates = self._find_by_title(title_query)

        filtered: list[Any] = []
        query_folded = title_query.casefold() if title_query is not None else None

        for window in candidates:
            if window_handle is not None:
                if self._window_handle(window) != window_handle:
                    continue

            if pid is not None:
                candidate_pid = self._window_pid(window)
                if candidate_pid != pid:
                    continue

            if query_folded is not None:
                title = self._window_title(window)
                if query_folded not in title.casefold():
                    continue

            filtered.append(window)

        return filtered

    def _resolve_single_target(
        self,
        *,
        title_query: Optional[str] = None,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> tuple[Any, dict[str, Any]]:
        title_query = _validate_title_query(title_query, required=False)
        window_handle = _validate_optional_int(
            window_handle,
            name="window_handle",
            maximum=MAX_WINDOW_HANDLE,
        )
        pid = _validate_optional_int(
            pid,
            name="pid",
            maximum=MAX_PID,
        )

        if title_query is None and window_handle is None and pid is None:
            raise _WindowToolError(
                "INVALID_ARGUMENT",
                "a title_query, window_handle, or pid selector is required",
            )

        candidates = self._target_candidates(
            title_query=title_query,
            window_handle=window_handle,
            pid=pid,
        )

        if not candidates:
            raise _WindowToolError(
                "NOT_FOUND",
                "no window matched the requested selector",
                {
                    "window_handle": window_handle,
                    "pid": pid,
                },
            )

        if len(candidates) > 1:
            descriptors = self._descriptors_sorted(candidates)
            bounded = descriptors[:MAX_AMBIGUITY_CANDIDATES]
            raise _WindowToolError(
                "AMBIGUOUS_TARGET",
                "window selector matched more than one target",
                {
                    "candidate_count": len(candidates),
                    "candidates": [
                        descriptor["selector"] for descriptor in bounded
                    ],
                },
            )

        window = candidates[0]
        return window, self._descriptor(window)

    def list_windows(
        self,
        max_results: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "list"
        try:
            try:
                limit = resolve_int_limit(max_results, WINDOW_RESULTS)
            except Exception as exc:
                if exc.__class__.__module__.startswith("tools.v1._shared"):
                    raise _WindowToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ) from exc
                raise

            descriptors = self._descriptors_sorted(self._enumerate_all())
            total = len(descriptors)
            returned = descriptors[:limit]
            return self._success(
                action,
                {
                    "returned_count": len(returned),
                    "total_count": total,
                    "windows": returned,
                },
                truncated=total > limit,
            )
        except _WindowToolError as exc:
            return self._failure(action, exc)

    def find_windows(
        self,
        title_query: str,
        max_results: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "find"
        try:
            query = _validate_title_query(title_query, required=True)
            assert query is not None
            try:
                limit = resolve_int_limit(max_results, WINDOW_RESULTS)
            except Exception as exc:
                if exc.__class__.__module__.startswith("tools.v1._shared"):
                    raise _WindowToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ) from exc
                raise

            descriptors = self._descriptors_sorted(self._find_by_title(query))
            total = len(descriptors)
            returned = descriptors[:limit]
            return self._success(
                action,
                {
                    "returned_count": len(returned),
                    "total_count": total,
                    "windows": returned,
                },
                truncated=total > limit,
            )
        except _WindowToolError as exc:
            return self._failure(action, exc)

    @staticmethod
    def _required_geometry_int(
        window: Any,
        property_name: str,
    ) -> int:
        try:
            value = getattr(window, property_name)
        except Exception as exc:
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window geometry property could not be read",
                {
                    "property": property_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        if type(value) is not int:
            raise _WindowToolError(
                "WINDOW_PROPERTY_FAILED",
                "required window geometry property has an invalid type",
                {"property": property_name},
            )
        return value

    def get_geometry(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "get_geometry"
        try:
            window, descriptor = self._resolve_single_target(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )

            left = self._required_geometry_int(window, "left")
            top = self._required_geometry_int(window, "top")
            width = self._required_geometry_int(window, "width")
            height = self._required_geometry_int(window, "height")
            self._required_geometry_int(window, "right")
            self._required_geometry_int(window, "bottom")

            if width < 0 or height < 0:
                raise _WindowToolError(
                    "WINDOW_PROPERTY_FAILED",
                    "window geometry dimensions must be non-negative",
                    {"property": "width_or_height"},
                )

            right = left + width
            bottom = top + height
            overall = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "right": right,
                "bottom": bottom,
            }

            warnings_list: list[str] = []
            client_area = None
            frame_elements = None
            try:
                frame = window.getClientFrame()
                if frame is not None:
                    client_left = self._frame_int(frame, "left")
                    client_top = self._frame_int(frame, "top")
                    client_right = self._frame_int(frame, "right")
                    client_bottom = self._frame_int(frame, "bottom")
                    client_width = max(0, client_right - client_left)
                    client_height = max(0, client_bottom - client_top)
                    client_area = {
                        "left": client_left,
                        "top": client_top,
                        "width": client_width,
                        "height": client_height,
                        "right": client_right,
                        "bottom": client_bottom,
                    }
                    frame_elements = {
                        "titlebar_height": client_top - top,
                        "border_left": client_left - left,
                        "border_right": right - client_right,
                        "border_bottom": bottom - client_bottom,
                    }
            except Exception:
                client_area = None
                frame_elements = None
                warnings_list.append("client geometry unavailable")

            return self._success(
                action,
                {
                    "window": descriptor,
                    "overall": overall,
                    "client_area": client_area,
                    "frame_elements": frame_elements,
                },
                warnings=warnings_list,
            )
        except _WindowToolError as exc:
            return self._failure(action, exc)

    @staticmethod
    def _frame_int(frame: Any, property_name: str) -> int:
        value = getattr(frame, property_name)
        if type(value) is not int:
            raise TypeError(property_name)
        return value

    def _confirmed_operation(
        self,
        action: str,
        method_name: str,
        *,
        title_query: Optional[str] = None,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        try:
            window, descriptor = self._resolve_single_target(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
            try:
                confirmed = getattr(window, method_name)(wait=True)
            except Exception as exc:
                raise _WindowToolError(
                    "WINDOW_OPERATION_FAILED",
                    "window operation failed in the backend",
                    {
                        "operation": action,
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            if confirmed is not True:
                raise _WindowToolError(
                    "WINDOW_OPERATION_NOT_CONFIRMED",
                    "window operation was not confirmed by the backend",
                    {"operation": action},
                )
            return self._success(
                action,
                {
                    "window": descriptor,
                    "confirmed": True,
                },
            )
        except _WindowToolError as exc:
            return self._failure(action, exc)

    def focus(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "focus"
        try:
            window, descriptor = self._resolve_single_target(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
            try:
                minimized = window.isMinimized
            except Exception as exc:
                raise _WindowToolError(
                    "WINDOW_PROPERTY_FAILED",
                    "window minimized state could not be read",
                    {
                        "property": "isMinimized",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            if type(minimized) is not bool:
                raise _WindowToolError(
                    "WINDOW_PROPERTY_FAILED",
                    "window minimized state has an invalid type",
                    {"property": "isMinimized"},
                )

            if minimized:
                try:
                    restored = window.restore(wait=True)
                except Exception as exc:
                    raise _WindowToolError(
                        "WINDOW_OPERATION_FAILED",
                        "window restore failed in the backend",
                        {
                            "operation": "restore",
                            "exception_type": type(exc).__name__,
                        },
                    ) from exc
                if restored is not True:
                    raise _WindowToolError(
                        "WINDOW_OPERATION_NOT_CONFIRMED",
                        "window restore was not confirmed by the backend",
                        {"operation": "restore"},
                    )

            try:
                activated = window.activate(wait=True)
            except Exception as exc:
                raise _WindowToolError(
                    "WINDOW_OPERATION_FAILED",
                    "window focus failed in the backend",
                    {
                        "operation": "focus",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            if activated is not True:
                raise _WindowToolError(
                    "WINDOW_OPERATION_NOT_CONFIRMED",
                    "window focus was not confirmed by the backend",
                    {"operation": "focus"},
                )

            return self._success(
                action,
                {
                    "window": descriptor,
                    "confirmed": True,
                },
            )
        except _WindowToolError as exc:
            return self._failure(action, exc)

    def minimize(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._confirmed_operation(
            "minimize",
            "minimize",
            title_query=title_query,
            window_handle=window_handle,
            pid=pid,
        )

    def maximize(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._confirmed_operation(
            "maximize",
            "maximize",
            title_query=title_query,
            window_handle=window_handle,
            pid=pid,
        )

    def restore(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._confirmed_operation(
            "restore",
            "restore",
            title_query=title_query,
            window_handle=window_handle,
            pid=pid,
        )

    def close(
        self,
        title_query: Optional[str] = None,
        *,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "close"
        try:
            window, descriptor = self._resolve_single_target(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
            try:
                window.close()
            except Exception as exc:
                raise _WindowToolError(
                    "WINDOW_OPERATION_FAILED",
                    "window close request failed in the backend",
                    {
                        "operation": "close",
                        "exception_type": type(exc).__name__,
                    },
                ) from exc

            deadline = time.monotonic() + CLOSE_VERIFY_TIMEOUT_SECONDS
            while True:
                try:
                    alive = window.isAlive
                except Exception as exc:
                    raise _WindowToolError(
                        "WINDOW_PROPERTY_FAILED",
                        "window liveness could not be verified after close",
                        {
                            "property": "isAlive",
                            "exception_type": type(exc).__name__,
                        },
                    ) from exc
                if type(alive) is not bool:
                    raise _WindowToolError(
                        "WINDOW_PROPERTY_FAILED",
                        "window liveness has an invalid type",
                        {"property": "isAlive"},
                    )
                if not alive:
                    return self._success(
                        action,
                        {
                            "window": descriptor,
                            "closed": True,
                        },
                    )
                if time.monotonic() >= deadline:
                    raise _WindowToolError(
                        "WINDOW_CLOSE_NOT_CONFIRMED",
                        "window remained alive after the close request",
                        {"timeout_seconds": CLOSE_VERIFY_TIMEOUT_SECONDS},
                    )
                time.sleep(CLOSE_VERIFY_POLL_SECONDS)
        except _WindowToolError as exc:
            return self._failure(action, exc)

    def execute(
        self,
        action: str,
        title_query: Optional[str] = None,
        window_handle: Optional[int] = None,
        pid: Optional[int] = None,
        max_results: Optional[int] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        aliases = {
            "list_windows": "list",
            "find_windows": "find",
            "search": "find",
            "geometry": "get_geometry",
            "get_bounds": "get_geometry",
            "info": "get_geometry",
            "focus_window": "focus",
            "activate": "focus",
            "close_window": "close",
            "restore_window": "restore",
        }
        canonical_action = aliases.get(action, action)

        if title_query is None:
            title_query = kwargs.get("title")
        if title_query is None:
            title_query = kwargs.get("query")
        if window_handle is None:
            window_handle = kwargs.get("handle")

        if canonical_action == "list":
            return self.list_windows(max_results=max_results)
        if canonical_action == "find":
            if title_query is None:
                return self._failure(
                    "find",
                    _WindowToolError(
                        "INVALID_ARGUMENT",
                        "title_query is required for find",
                    ),
                )
            return self.find_windows(
                title_query=title_query,
                max_results=max_results,
            )
        if canonical_action == "get_geometry":
            return self.get_geometry(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
        if canonical_action == "focus":
            return self.focus(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
        if canonical_action == "close":
            return self.close(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
        if canonical_action == "minimize":
            return self.minimize(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
        if canonical_action == "maximize":
            return self.maximize(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )
        if canonical_action == "restore":
            return self.restore(
                title_query=title_query,
                window_handle=window_handle,
                pid=pid,
            )

        return failure_result(
            tool="window_tool",
            action=action if isinstance(action, str) and action else "unknown",
            version=WINDOW_TOOL_VERSION,
            code="INVALID_ARGUMENT",
            message="unsupported window action",
            details={},
        )


_default_window_tool = WindowTool()


def list_windows(
    max_results: Optional[int] = None,
) -> dict[str, Any]:
    return _default_window_tool.list_windows(max_results=max_results)


def find_windows(
    title_query: str,
    max_results: Optional[int] = None,
) -> dict[str, Any]:
    return _default_window_tool.find_windows(
        title_query=title_query,
        max_results=max_results,
    )


def focus_window(
    title_query: Optional[str] = None,
    *,
    window_handle: Optional[int] = None,
    pid: Optional[int] = None,
) -> dict[str, Any]:
    return _default_window_tool.focus(
        title_query=title_query,
        window_handle=window_handle,
        pid=pid,
    )


def close_window(
    title_query: Optional[str] = None,
    *,
    window_handle: Optional[int] = None,
    pid: Optional[int] = None,
) -> dict[str, Any]:
    return _default_window_tool.close(
        title_query=title_query,
        window_handle=window_handle,
        pid=pid,
    )


def restore_window(
    title_query: Optional[str] = None,
    *,
    window_handle: Optional[int] = None,
    pid: Optional[int] = None,
) -> dict[str, Any]:
    return _default_window_tool.restore(
        title_query=title_query,
        window_handle=window_handle,
        pid=pid,
    )


def run(action: str, **kwargs: Any) -> dict[str, Any]:
    return _default_window_tool.execute(action=action, **kwargs)
