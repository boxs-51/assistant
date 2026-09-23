from __future__ import annotations

import heapq
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional

from tools.v1._shared.contracts import failure_result, success_result, tool_result_schema
from tools.v1._shared.limits import IntLimitSpec, resolve_int_limit

GLOB_TOOL_VERSION = "2.0.0"
MAX_GLOB_PATTERN_CHARS = 2048
MAX_GLOB_ROOT_CHARS = 4096
GLOB_MAX_RESULTS_HARD = 5000
GLOB_DEFAULT_MAX_RESULTS = 500


TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "find_by_glob",
    "version": GLOB_TOOL_VERSION,
    "description": (
        "Tìm file/thư mục theo glob pattern trong một root_dir có boundary rõ ràng. "
        "Kết quả trả về theo ToolResult có cấu trúc."
    ),
    "expose_root": False,
    "exports": [
        {
            "id": "glob.find",
            "version": "1.0",
            "name": "glob.find",
            "description": "Tìm file hoặc thư mục theo glob pattern trong một root_dir.",
            "bind": {},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pattern": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_GLOB_PATTERN_CHARS,
                    },
                    "root_dir": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_GLOB_ROOT_CHARS,
                    },
                    "recursive": {"type": "boolean"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": GLOB_MAX_RESULTS_HARD,
                    },
                },
                "required": ["pattern"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "LOW",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        }
    ],
    # Legacy physical description remains for direct Python consumers only.
    "base_risk": "LOW",
    "effects": ["READ"],
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_GLOB_PATTERN_CHARS,
            },
            "root_dir": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_GLOB_ROOT_CHARS,
            },
            "recursive": {"type": "boolean"},
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": GLOB_MAX_RESULTS_HARD,
            },
        },
        "required": ["pattern"],
    },
}

class _GlobToolError(Exception):
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


def _canonical_path(path: Path) -> str:
    return Path(os.path.abspath(os.fspath(path))).as_posix()


def _validate_root(root_dir: str) -> tuple[Path, str]:
    if not isinstance(root_dir, str) or not root_dir:
        raise _GlobToolError("INVALID_ARGUMENT", "root_dir must be a non-empty string")
    if "\x00" in root_dir:
        raise _GlobToolError("INVALID_ARGUMENT", "root_dir contains a NUL character")
    if len(root_dir) > MAX_GLOB_ROOT_CHARS:
        raise _GlobToolError(
            "INVALID_ARGUMENT",
            f"root_dir exceeds the hard limit of {MAX_GLOB_ROOT_CHARS} characters",
        )

    path = Path(os.path.abspath(root_dir))
    canonical = path.as_posix()
    if len(canonical) > MAX_GLOB_ROOT_CHARS:
        raise _GlobToolError(
            "INVALID_ARGUMENT",
            f"canonical root_dir exceeds the hard limit of {MAX_GLOB_ROOT_CHARS} characters",
        )
    if not path.exists():
        raise _GlobToolError(
            "GLOB_ROOT_NOT_FOUND",
            "glob root directory does not exist",
            {"root_dir": canonical},
        )
    if not path.is_dir():
        raise _GlobToolError(
            "GLOB_ROOT_NOT_DIRECTORY",
            "glob root path is not a directory",
            {"root_dir": canonical},
        )
    return path, canonical


def _validate_pattern(pattern: str) -> str:
    if not isinstance(pattern, str) or not pattern.strip():
        raise _GlobToolError("INVALID_ARGUMENT", "glob pattern must be non-empty")
    if "\x00" in pattern:
        raise _GlobToolError("GLOB_PATTERN_OUTSIDE_ROOT", "glob pattern contains NUL")
    if len(pattern) > MAX_GLOB_PATTERN_CHARS:
        raise _GlobToolError(
            "INVALID_ARGUMENT",
            f"glob pattern exceeds the hard limit of {MAX_GLOB_PATTERN_CHARS} characters",
        )

    if PurePosixPath(pattern).is_absolute() or PureWindowsPath(pattern).is_absolute():
        raise _GlobToolError(
            "GLOB_PATTERN_OUTSIDE_ROOT",
            "absolute glob patterns are not allowed",
        )
    if PureWindowsPath(pattern).drive:
        raise _GlobToolError(
            "GLOB_PATTERN_OUTSIDE_ROOT",
            "drive-qualified glob patterns are not allowed",
        )

    segments = [segment for segment in re.split(r"[\\/]+", pattern) if segment]
    if any(segment == ".." for segment in segments):
        raise _GlobToolError(
            "GLOB_PATTERN_OUTSIDE_ROOT",
            "parent traversal is not allowed in glob patterns",
        )
    return pattern


class GlobSearchTool:
    """Deterministic bounded glob search rooted at one directory."""

    def __init__(self, default_max_results: int = GLOB_DEFAULT_MAX_RESULTS) -> None:
        self._limit_spec = IntLimitSpec(
            "max_results",
            default=default_max_results,
            minimum=1,
            maximum=GLOB_MAX_RESULTS_HARD,
        )
        self.default_max_results = default_max_results

    def _success(
        self,
        data: dict[str, Any],
        *,
        truncated: bool = False,
    ) -> dict[str, Any]:
        return success_result(
            tool="find_by_glob",
            action="find",
            version=GLOB_TOOL_VERSION,
            data=data,
            truncated=truncated,
        )

    def _failure(self, error: _GlobToolError) -> dict[str, Any]:
        return failure_result(
            tool="find_by_glob",
            action="find",
            version=GLOB_TOOL_VERSION,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    def find(
        self,
        pattern: str,
        root_dir: str = ".",
        recursive: bool = True,
        max_results: Optional[int] = None,
    ) -> dict[str, Any]:
        try:
            normalized_pattern = _validate_pattern(pattern)
            base_path, canonical_root = _validate_root(root_dir)
            if type(recursive) is not bool:
                raise _GlobToolError(
                    "INVALID_ARGUMENT",
                    "recursive must be a boolean",
                )
            try:
                limit = resolve_int_limit(max_results, self._limit_spec)
            except Exception as exc:
                if exc.__class__.__module__.startswith("tools.v1._shared"):
                    raise _GlobToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ) from exc
                raise

            has_recursive_prefix = (
                normalized_pattern == "**"
                or normalized_pattern.startswith("**/")
                or normalized_pattern.startswith("**\\")
            )
            if recursive and not has_recursive_prefix:
                search_pattern = f"**/{normalized_pattern}"
            else:
                search_pattern = normalized_pattern

            def sort_key(path: Path) -> tuple[str, str]:
                canonical = _canonical_path(path)
                return canonical.casefold(), canonical

            try:
                selected_plus_one = heapq.nsmallest(
                    limit + 1,
                    base_path.glob(search_pattern),
                    key=sort_key,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise _GlobToolError(
                    "GLOB_IO_ERROR",
                    "glob traversal failed",
                    {
                        "root_dir": canonical_root,
                        "exception_type": type(exc).__name__,
                    },
                ) from exc

            truncated = len(selected_plus_one) > limit
            selected = selected_plus_one[:limit]
            matches: list[dict[str, Any]] = []
            for path in selected:
                is_symlink = path.is_symlink()
                try:
                    if path.is_file():
                        kind = "file"
                    elif path.is_dir():
                        kind = "directory"
                    else:
                        kind = "other"
                except OSError:
                    kind = "other"
                matches.append(
                    {
                        "path": _canonical_path(path),
                        "kind": kind,
                        "is_symlink": is_symlink,
                    }
                )

            return self._success(
                {
                    "root_dir": canonical_root,
                    "pattern": normalized_pattern,
                    "recursive": recursive,
                    "returned_count": len(matches),
                    "matches": matches,
                },
                truncated=truncated,
            )
        except _GlobToolError as exc:
            return self._failure(exc)

    def execute(
        self,
        pattern: str,
        root_dir: str = ".",
        recursive: bool = True,
        max_results: Optional[int] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return self.find(
            pattern=pattern,
            root_dir=root_dir,
            recursive=recursive,
            max_results=max_results,
        )


_default_glob_tool = GlobSearchTool()


def run(
    pattern: str,
    root_dir: str = ".",
    recursive: bool = True,
    max_results: Optional[int] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _default_glob_tool.execute(
        pattern=pattern,
        root_dir=root_dir,
        recursive=recursive,
        max_results=max_results,
        **kwargs,
    )
