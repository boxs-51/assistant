from __future__ import annotations

import codecs
import difflib
import hashlib
import io
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from tools.v1._shared.contracts import failure_result, success_result, tool_result_schema
from tools.v1._shared.limits import IntLimitSpec, resolve_int_limit

FILE_TOOL_VERSION = "2.0.0"

MAX_PATH_CHARS = 4096
MAX_ENCODING_CHARS = 64
MAX_FILE_COUNT = 32
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 32 * 1024 * 1024
MAX_WRITE_CONTENT_BYTES = 8 * 1024 * 1024
MAX_QUERY_COUNT = 32
MAX_QUERY_CHARS = 2048
MAX_REPLACEMENT_CHARS = 65536
MAX_REGEX_LINE_CHARS = 1_000_000
MAX_MATCH_SNIPPET_CHARS = 2000
MAX_TOTAL_RETURNED_MATCHES = 1000
MAX_TOTAL_MATCH_OCCURRENCES = 1_000_000

READ_MAX_CHARS = IntLimitSpec("max_chars", default=100_000, minimum=1, maximum=1_000_000)
START_LINE = IntLimitSpec("start_line", default=1, minimum=1, maximum=10_000_000)
NUM_LINES = IntLimitSpec("num_lines", default=1, minimum=1, maximum=100_000)
MAX_RESULTS_PER_FILE = IntLimitSpec(
    "max_results_per_file", default=50, minimum=1, maximum=500
)

DEFAULT_DANGER_PATTERNS: Tuple[str, ...] = (
    r"\.env$",
    r"\.pem$",
    r"id_rsa",
    r"config/AGENT\.md$",
    r"\.bashrc$",
    r"\.zshrc$",
)


def _string_or_string_array_schema(*, item_max_length: int) -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string", "minLength": 1, "maxLength": item_max_length},
            {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_FILE_COUNT,
                "items": {"type": "string", "minLength": 1, "maxLength": item_max_length},
            },
        ]
    }


TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "file_tool",
    "version": FILE_TOOL_VERSION,
    "description": (
        "Thao tác tệp văn bản local: read, write/append, search và replace. "
        "Kết quả trả về theo ToolResult có cấu trúc."
    ),
    "expose_root": False,
    "exports": [
        {
            "id": "file.read",
            "version": "1.0",
            "name": "file.read",
            "description": "Đọc một tệp văn bản local có phân trang và giới hạn đầu ra.",
            "bind": {"action": "read"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_paths": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": MAX_PATH_CHARS},
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 1,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_PATH_CHARS,
                                },
                            },
                        ]
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": START_LINE.minimum,
                        "maximum": START_LINE.maximum,
                    },
                    "num_lines": {
                        "type": "integer",
                        "minimum": NUM_LINES.minimum,
                        "maximum": NUM_LINES.maximum,
                    },
                    "encoding": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ENCODING_CHARS,
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": READ_MAX_CHARS.minimum,
                        "maximum": READ_MAX_CHARS.maximum,
                    },
                },
                "required": ["file_paths"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "HIGH",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
        },
        {
            "id": "file.search",
            "version": "1.0",
            "name": "file.search",
            "description": "Tìm chuỗi hoặc biểu thức chính quy trong một hay nhiều tệp văn bản.",
            "bind": {"action": "search"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_paths": _string_or_string_array_schema(
                        item_max_length=MAX_PATH_CHARS
                    ),
                    "queries": {
                        "anyOf": [
                            {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_QUERY_CHARS,
                            },
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": MAX_QUERY_COUNT,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_QUERY_CHARS,
                                },
                            },
                        ]
                    },
                    "use_regex": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                    "max_results_per_file": {
                        "type": "integer",
                        "minimum": MAX_RESULTS_PER_FILE.minimum,
                        "maximum": MAX_RESULTS_PER_FILE.maximum,
                    },
                    "encoding": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ENCODING_CHARS,
                    },
                },
                "required": ["file_paths", "queries"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["READ"],
            "base_risk": "HIGH",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
        },
        {
            "id": "file.write",
            "version": "1.0",
            "name": "file.write",
            "description": "Ghi đè nội dung một tệp văn bản local.",
            "bind": {"action": "write", "mode": "w"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_paths": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": MAX_PATH_CHARS},
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 1,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_PATH_CHARS,
                                },
                            },
                        ]
                    },
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ENCODING_CHARS,
                    },
                },
                "required": ["file_paths", "content"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "IDEMPOTENT",
            "effects": ["WRITE"],
            "base_risk": "HIGH",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
        },
        {
            "id": "file.append",
            "version": "1.0",
            "name": "file.append",
            "description": "Nối thêm nội dung vào cuối một tệp văn bản local.",
            "bind": {"action": "write", "mode": "a"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_paths": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": MAX_PATH_CHARS},
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 1,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_PATH_CHARS,
                                },
                            },
                        ]
                    },
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ENCODING_CHARS,
                    },
                },
                "required": ["file_paths", "content"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "NON_IDEMPOTENT",
            "effects": ["WRITE"],
            "base_risk": "HIGH",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
        },
        {
            "id": "file.replace",
            "version": "1.0",
            "name": "file.replace",
            "description": "Thay thế nội dung trong một hay nhiều tệp văn bản local.",
            "bind": {"action": "replace"},
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_paths": _string_or_string_array_schema(
                        item_max_length=MAX_PATH_CHARS
                    ),
                    "queries": {
                        "anyOf": [
                            {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_QUERY_CHARS,
                            },
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": MAX_QUERY_COUNT,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_QUERY_CHARS,
                                },
                            },
                        ]
                    },
                    "replacements": {
                        "anyOf": [
                            {"type": "string", "maxLength": MAX_REPLACEMENT_CHARS},
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": MAX_QUERY_COUNT,
                                "items": {
                                    "type": "string",
                                    "maxLength": MAX_REPLACEMENT_CHARS,
                                },
                            },
                        ]
                    },
                    "use_regex": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                    "max_results_per_file": {
                        "type": "integer",
                        "minimum": MAX_RESULTS_PER_FILE.minimum,
                        "maximum": MAX_RESULTS_PER_FILE.maximum,
                    },
                    "encoding": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ENCODING_CHARS,
                    },
                },
                "required": ["file_paths", "queries", "replacements"],
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["WRITE"],
            "base_risk": "HIGH",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
        },
    ],
    # Legacy physical description remains for direct Python consumers only.
    "base_risk": "HIGH",
    "effects": ["READ", "WRITE"],
    "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "search", "replace"],
            },
            "file_paths": _string_or_string_array_schema(item_max_length=MAX_PATH_CHARS),
            "content": {"type": "string"},
            "mode": {"type": "string", "enum": ["w", "a"]},
            "queries": {
                "anyOf": [
                    {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                    {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_QUERY_COUNT,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_QUERY_CHARS,
                        },
                    },
                ]
            },
            "replacements": {
                "anyOf": [
                    {"type": "string", "maxLength": MAX_REPLACEMENT_CHARS},
                    {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_QUERY_COUNT,
                        "items": {
                            "type": "string",
                            "maxLength": MAX_REPLACEMENT_CHARS,
                        },
                    },
                ]
            },
            "encoding": {"type": "string", "minLength": 1, "maxLength": MAX_ENCODING_CHARS},
            "start_line": {
                "type": "integer",
                "minimum": START_LINE.minimum,
                "maximum": START_LINE.maximum,
            },
            "num_lines": {
                "type": "integer",
                "minimum": NUM_LINES.minimum,
                "maximum": NUM_LINES.maximum,
            },
            "max_chars": {
                "type": "integer",
                "minimum": READ_MAX_CHARS.minimum,
                "maximum": READ_MAX_CHARS.maximum,
            },
            "use_regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "max_results_per_file": {
                "type": "integer",
                "minimum": MAX_RESULTS_PER_FILE.minimum,
                "maximum": MAX_RESULTS_PER_FILE.maximum,
            },
        },
        "required": ["action"],
    },
}

class _FileToolError(Exception):
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


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    canonical_path: str
    exists: bool
    is_symlink: bool
    size_bytes: int
    sha256: Optional[str]
    mode: Optional[int]
    raw_bytes: bytes
    text: str
    encoding: str


@dataclass
class _FileMutationPlan:
    snapshot: _FileSnapshot
    final_text: str
    final_bytes: bytes
    after_sha256: str
    replacement_count: int = 0
    change_records: Optional[list[dict[str, Any]]] = None
    reported_change_count: int = 0


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_path(path: str) -> tuple[Path, str]:
    if not isinstance(path, str) or not path:
        raise _FileToolError("INVALID_ARGUMENT", "file path must be a non-empty string")
    if "\x00" in path:
        raise _FileToolError("INVALID_ARGUMENT", "file path contains a NUL character")
    if len(path) > MAX_PATH_CHARS:
        raise _FileToolError(
            "INVALID_ARGUMENT",
            f"file path exceeds the hard limit of {MAX_PATH_CHARS} characters",
        )
    absolute = Path(os.path.abspath(path))
    canonical = absolute.as_posix()
    if len(canonical) > MAX_PATH_CHARS:
        raise _FileToolError(
            "INVALID_ARGUMENT",
            f"canonical file path exceeds the hard limit of {MAX_PATH_CHARS} characters",
        )
    return absolute, canonical


def _validate_encoding(encoding: Optional[str], default_encoding: str) -> str:
    value = default_encoding if encoding is None else encoding
    if not isinstance(value, str) or not value:
        raise _FileToolError("FILE_ENCODING_INVALID", "encoding must be a non-empty string")
    if len(value) > MAX_ENCODING_CHARS:
        raise _FileToolError("FILE_ENCODING_INVALID", "encoding name is too long")
    try:
        return codecs.lookup(value).name
    except LookupError as exc:
        raise _FileToolError(
            "FILE_ENCODING_INVALID",
            "unknown text encoding",
        ) from exc


def _bounded_line_snippet(line: str, start: int, end: int) -> tuple[str, int, int]:
    if len(line) <= MAX_MATCH_SNIPPET_CHARS:
        return line, 0, len(line)

    width = MAX_MATCH_SNIPPET_CHARS
    if end - start >= width:
        snippet_start = max(0, min(start, len(line) - width))
    else:
        left = max(0, start - width // 3)
        snippet_start = min(left, max(0, len(line) - width))
        if end > snippet_start + width:
            snippet_start = min(max(0, end - width), len(line) - width)
    snippet_end = min(len(line), snippet_start + width)
    return line[snippet_start:snippet_end], snippet_start, snippet_end


class FileTool:
    """Bounded text-file operations with structured ToolResult output."""

    def __init__(
        self,
        default_encoding: str = "utf-8",
        confirm_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
        danger_patterns: Optional[List[str]] = None,
    ) -> None:
        self.default_encoding = _validate_encoding(default_encoding, "utf-8")
        self.confirm_callback = confirm_callback
        configured = DEFAULT_DANGER_PATTERNS if danger_patterns is None else tuple(danger_patterns)
        self.danger_patterns = list(configured)
        self._danger_regexes = tuple(re.compile(pattern, re.IGNORECASE) for pattern in configured)

    def _success(
        self,
        action: str,
        data: Any,
        *,
        truncated: bool = False,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return success_result(
            tool="file_tool",
            action=action,
            version=FILE_TOOL_VERSION,
            data=data,
            truncated=truncated,
            warnings=warnings,
        )

    def _failure(
        self,
        action: str,
        error: _FileToolError,
    ) -> dict[str, Any]:
        return failure_result(
            tool="file_tool",
            action=action,
            version=FILE_TOOL_VERSION,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    def _is_dangerous_path(self, file_path: str) -> bool:
        _, canonical = _canonical_path(file_path)
        return any(regex.search(canonical) for regex in self._danger_regexes)

    def _generate_diff(self, old_text: str, new_text: str, file_path: str) -> str:
        return "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm="",
            )
        )

    def _request_confirmation(
        self,
        action: str,
        snapshot: _FileSnapshot,
        new_content: str,
    ) -> bool:
        if self.confirm_callback is None:
            return True
        payload = {
            "action": action,
            "file_path": snapshot.canonical_path,
            "old_content": snapshot.text,
            "new_content": new_content,
            "diff": self._generate_diff(
                snapshot.text,
                new_content,
                snapshot.canonical_path,
            ),
            "has_changes": snapshot.text != new_content or not snapshot.exists,
            "is_dangerous": self._is_dangerous_path(snapshot.canonical_path),
        }
        try:
            return bool(self.confirm_callback(payload))
        except Exception as exc:
            raise _FileToolError(
                "FILE_IO_ERROR",
                "confirmation callback failed",
                {"path": snapshot.canonical_path, "exception_type": type(exc).__name__},
            ) from exc

    def _read_raw_bounded(self, path: Path, canonical_path: str) -> bytes:
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_TEXT_FILE_BYTES + 1)
        except OSError as exc:
            raise _FileToolError(
                "FILE_IO_ERROR",
                "unable to read file",
                {"path": canonical_path, "exception_type": type(exc).__name__},
            ) from exc

        if len(raw) > MAX_TEXT_FILE_BYTES:
            raise _FileToolError(
                "FILE_TOO_LARGE",
                "file exceeds the text-file byte limit",
                {
                    "path": canonical_path,
                    "max_bytes": MAX_TEXT_FILE_BYTES,
                },
            )
        return raw

    def _snapshot_text_file(
        self,
        file_path: str,
        *,
        encoding: str,
        require_exists: bool,
        for_mutation: bool,
    ) -> _FileSnapshot:
        path, canonical = _canonical_path(file_path)
        is_symlink = path.is_symlink()

        if for_mutation and is_symlink:
            raise _FileToolError(
                "FILE_SYMLINK_MUTATION_BLOCKED",
                "mutation through a symlink path is not allowed",
                {"path": canonical},
            )

        exists = path.exists()
        if not exists:
            if require_exists:
                raise _FileToolError(
                    "FILE_NOT_FOUND",
                    "file does not exist",
                    {"path": canonical},
                )
            return _FileSnapshot(
                path=path,
                canonical_path=canonical,
                exists=False,
                is_symlink=is_symlink,
                size_bytes=0,
                sha256=None,
                mode=None,
                raw_bytes=b"",
                text="",
                encoding=encoding,
            )

        if not path.is_file():
            raise _FileToolError(
                "FILE_NOT_REGULAR",
                "path is not a regular file",
                {"path": canonical},
            )

        try:
            file_stat = path.stat()
        except OSError as exc:
            raise _FileToolError(
                "FILE_IO_ERROR",
                "unable to stat file",
                {"path": canonical, "exception_type": type(exc).__name__},
            ) from exc

        if file_stat.st_size > MAX_TEXT_FILE_BYTES:
            raise _FileToolError(
                "FILE_TOO_LARGE",
                "file exceeds the text-file byte limit",
                {
                    "path": canonical,
                    "size_bytes": int(file_stat.st_size),
                    "max_bytes": MAX_TEXT_FILE_BYTES,
                },
            )

        raw = self._read_raw_bounded(path, canonical)
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise _FileToolError(
                "FILE_DECODE_ERROR",
                "file cannot be decoded with the requested encoding",
                {
                    "path": canonical,
                    "encoding": encoding,
                    "byte_start": exc.start,
                    "byte_end": exc.end,
                },
            ) from exc

        return _FileSnapshot(
            path=path,
            canonical_path=canonical,
            exists=True,
            is_symlink=is_symlink,
            size_bytes=len(raw),
            sha256=_sha256(raw),
            mode=stat.S_IMODE(file_stat.st_mode),
            raw_bytes=raw,
            text=text,
            encoding=encoding,
        )

    def _snapshot_many(
        self,
        paths: list[str],
        *,
        encoding: str,
        for_mutation: bool,
    ) -> list[_FileSnapshot]:
        snapshots: list[_FileSnapshot] = []
        total_bytes = 0
        for path in paths:
            snapshot = self._snapshot_text_file(
                path,
                encoding=encoding,
                require_exists=True,
                for_mutation=for_mutation,
            )
            total_bytes += snapshot.size_bytes
            if total_bytes > MAX_TOTAL_FILE_BYTES:
                raise _FileToolError(
                    "FILE_TOO_LARGE",
                    "aggregate input files exceed the per-call byte budget",
                    {
                        "max_total_bytes": MAX_TOTAL_FILE_BYTES,
                        "observed_total_bytes": total_bytes,
                    },
                )
            snapshots.append(snapshot)
        return snapshots

    def _encode_text(self, text: str, encoding: str, *, path: str) -> bytes:
        try:
            raw = text.encode(encoding, errors="strict")
        except UnicodeEncodeError as exc:
            raise _FileToolError(
                "FILE_DECODE_ERROR",
                "text cannot be encoded with the requested encoding",
                {
                    "path": path,
                    "encoding": encoding,
                    "char_start": exc.start,
                    "char_end": exc.end,
                },
            ) from exc
        if len(raw) > MAX_TEXT_FILE_BYTES:
            raise _FileToolError(
                "FILE_TOO_LARGE",
                "resulting file exceeds the text-file byte limit",
                {
                    "path": path,
                    "size_bytes": len(raw),
                    "max_bytes": MAX_TEXT_FILE_BYTES,
                },
            )
        return raw

    def _check_snapshot_unchanged(self, snapshot: _FileSnapshot) -> None:
        path = snapshot.path
        if not snapshot.exists:
            if path.exists() or path.is_symlink():
                raise _FileToolError(
                    "FILE_CHANGED_DURING_OPERATION",
                    "destination appeared after the operation was planned",
                    {"path": snapshot.canonical_path},
                )
            return

        if path.is_symlink() or not path.exists() or not path.is_file():
            raise _FileToolError(
                "FILE_CHANGED_DURING_OPERATION",
                "destination type changed after the operation was planned",
                {"path": snapshot.canonical_path},
            )

        try:
            current = self._read_raw_bounded(path, snapshot.canonical_path)
        except _FileToolError as exc:
            raise _FileToolError(
                "FILE_CHANGED_DURING_OPERATION",
                "destination could not be verified before commit",
                {"path": snapshot.canonical_path, "cause": exc.code},
            ) from exc

        if _sha256(current) != snapshot.sha256:
            raise _FileToolError(
                "FILE_CHANGED_DURING_OPERATION",
                "destination content changed after the operation was planned",
                {"path": snapshot.canonical_path},
            )

    def _replace_bytes(
        self,
        snapshot: _FileSnapshot,
        final_bytes: bytes,
        *,
        preserve_mode: Optional[int],
    ) -> None:
        path = snapshot.path
        parent = path.parent
        temp_name: Optional[str] = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=f".{path.name}.tools-v1-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(final_bytes)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass

            if preserve_mode is not None and os.name != "nt":
                os.chmod(temp_name, preserve_mode)

            self._check_snapshot_unchanged(snapshot)
            os.replace(temp_name, path)
            temp_name = None
        except _FileToolError:
            raise
        except OSError as exc:
            raise _FileToolError(
                "FILE_IO_ERROR",
                "atomic file replacement failed",
                {
                    "path": snapshot.canonical_path,
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def _rollback_committed_plan(self, plan: _FileMutationPlan) -> str:
        snapshot = plan.snapshot
        path = snapshot.path
        if not snapshot.exists:
            if not path.exists() or path.is_symlink() or not path.is_file():
                return "conflict"
            try:
                current = self._read_raw_bounded(path, snapshot.canonical_path)
            except _FileToolError:
                return "failure"
            if _sha256(current) != plan.after_sha256:
                return "conflict"
            try:
                path.unlink()
                return "rolled_back"
            except OSError:
                return "failure"

        if path.is_symlink() or not path.exists() or not path.is_file():
            return "conflict"
        try:
            current = self._read_raw_bounded(path, snapshot.canonical_path)
        except _FileToolError:
            return "failure"
        if _sha256(current) != plan.after_sha256:
            return "conflict"

        rollback_snapshot = _FileSnapshot(
            path=path,
            canonical_path=snapshot.canonical_path,
            exists=True,
            is_symlink=False,
            size_bytes=len(current),
            sha256=_sha256(current),
            mode=snapshot.mode,
            raw_bytes=current,
            text=plan.final_text,
            encoding=snapshot.encoding,
        )
        try:
            self._replace_bytes(
                rollback_snapshot,
                snapshot.raw_bytes,
                preserve_mode=snapshot.mode,
            )
            return "rolled_back"
        except _FileToolError:
            return "failure"

    def _validate_file_paths(
        self,
        file_paths: Union[str, List[str], None],
        *,
        require_existing: bool,
    ) -> list[str]:
        if isinstance(file_paths, str):
            raw_paths = [file_paths]
        elif isinstance(file_paths, list):
            raw_paths = file_paths
        else:
            raise _FileToolError(
                "INVALID_ARGUMENT",
                "file_paths must be a string or a non-empty list of strings",
            )

        if not raw_paths or len(raw_paths) > MAX_FILE_COUNT:
            raise _FileToolError(
                "INVALID_ARGUMENT",
                f"file_paths must contain between 1 and {MAX_FILE_COUNT} paths",
            )

        canonical_paths: list[str] = []
        concrete_paths: list[Path] = []
        seen: set[str] = set()
        for index, raw_path in enumerate(raw_paths):
            if not isinstance(raw_path, str) or not raw_path:
                raise _FileToolError(
                    "INVALID_ARGUMENT",
                    "file_paths contains an invalid path",
                    {"index": index},
                )
            path, canonical = _canonical_path(raw_path)
            key = os.path.normcase(os.path.abspath(raw_path))
            if key in seen:
                raise _FileToolError(
                    "INVALID_ARGUMENT",
                    "file_paths contains duplicate paths",
                    {"index": index, "path": canonical},
                )
            seen.add(key)

            if require_existing and path.exists():
                for previous in concrete_paths:
                    try:
                        if previous.exists() and os.path.samefile(previous, path):
                            raise _FileToolError(
                                "INVALID_ARGUMENT",
                                "file_paths contains aliases to the same file",
                                {"path": canonical},
                            )
                    except OSError:
                        continue
            canonical_paths.append(canonical)
            concrete_paths.append(path)

        return canonical_paths

    def _validate_queries(
        self,
        queries: Union[str, List[str], None],
        replacements: Optional[Union[str, List[str]]] = None,
        *,
        is_replace: bool,
    ) -> tuple[list[str], Optional[list[str]]]:
        if isinstance(queries, str):
            query_list = [queries]
        elif isinstance(queries, list):
            query_list = queries
        else:
            raise _FileToolError("INVALID_ARGUMENT", "queries must be a string or list")

        if not query_list or len(query_list) > MAX_QUERY_COUNT:
            raise _FileToolError(
                "INVALID_ARGUMENT",
                f"queries must contain between 1 and {MAX_QUERY_COUNT} items",
            )
        for index, query in enumerate(query_list):
            if not isinstance(query, str) or not query or len(query) > MAX_QUERY_CHARS:
                raise _FileToolError(
                    "INVALID_ARGUMENT",
                    "query is empty, non-string, or exceeds the hard length limit",
                    {"query_index": index},
                )

        replacement_list: Optional[list[str]] = None
        if is_replace:
            if isinstance(replacements, str):
                replacement_list = [replacements] * len(query_list)
            elif isinstance(replacements, list):
                replacement_list = replacements
            else:
                raise _FileToolError(
                    "INVALID_ARGUMENT",
                    "replace requires replacements",
                )
            if len(replacement_list) != len(query_list):
                raise _FileToolError(
                    "INVALID_ARGUMENT",
                    "replacements count must equal queries count",
                )
            for index, replacement in enumerate(replacement_list):
                if not isinstance(replacement, str) or len(replacement) > MAX_REPLACEMENT_CHARS:
                    raise _FileToolError(
                        "INVALID_ARGUMENT",
                        "replacement is non-string or exceeds the hard length limit",
                        {"query_index": index},
                    )
        return query_list, replacement_list

    def _compile_patterns(
        self,
        queries: list[str],
        *,
        use_regex: bool,
        case_sensitive: bool,
    ) -> list[re.Pattern[str]]:
        if type(use_regex) is not bool or type(case_sensitive) is not bool:
            raise _FileToolError(
                "INVALID_ARGUMENT",
                "use_regex and case_sensitive must be booleans",
            )
        flags = 0 if case_sensitive else re.IGNORECASE
        patterns: list[re.Pattern[str]] = []
        for index, query in enumerate(queries):
            try:
                patterns.append(re.compile(query if use_regex else re.escape(query), flags))
            except re.error as exc:
                raise _FileToolError(
                    "FILE_REGEX_INVALID",
                    "invalid regular expression",
                    {"query_index": index},
                ) from exc
        return patterns

    def _bounded_substitute(
        self,
        pattern: re.Pattern[str],
        text: str,
        replacement: str,
        *,
        use_regex: bool,
        path: str,
        line: int,
        query_index: int,
        remaining_occurrences: int,
    ) -> tuple[str, int]:
        output = io.StringIO()
        output_chars = 0
        last_end = 0
        count = 0

        try:
            for match in pattern.finditer(text):
                if count >= remaining_occurrences:
                    raise _FileToolError(
                        "OUTPUT_LIMIT_EXCEEDED",
                        "replacement occurrence budget exceeded",
                        {
                            "path": path,
                            "line": line,
                            "query_index": query_index,
                            "max_occurrences": MAX_TOTAL_MATCH_OCCURRENCES,
                        },
                    )

                unchanged = text[last_end:match.start()]
                projected = output_chars + len(unchanged)
                if projected > MAX_REGEX_LINE_CHARS:
                    raise _FileToolError(
                        "OUTPUT_LIMIT_EXCEEDED",
                        "a transformed text line exceeds the regex processing limit",
                        {
                            "path": path,
                            "line": line,
                            "query_index": query_index,
                            "max_chars": MAX_REGEX_LINE_CHARS,
                        },
                    )
                output.write(unchanged)
                output_chars = projected

                if use_regex:
                    # Guard match.expand() before it can materialize a very large
                    # backreference expansion. Every replacement backreference
                    # contains a backslash, so this is deliberately conservative.
                    group_lengths = [len(match.group(0) or "")]
                    group_lengths.extend(len(group or "") for group in match.groups())
                    max_group_length = max(group_lengths, default=0)
                    conservative_expand_bound = (
                        len(replacement)
                        + replacement.count("\\") * max_group_length
                    )
                    if output_chars + conservative_expand_bound > MAX_REGEX_LINE_CHARS:
                        raise _FileToolError(
                            "OUTPUT_LIMIT_EXCEEDED",
                            "regex replacement expansion exceeds the line budget",
                            {
                                "path": path,
                                "line": line,
                                "query_index": query_index,
                                "max_chars": MAX_REGEX_LINE_CHARS,
                            },
                        )
                    expanded = match.expand(replacement)
                else:
                    expanded = replacement

                if output_chars + len(expanded) > MAX_REGEX_LINE_CHARS:
                    raise _FileToolError(
                        "OUTPUT_LIMIT_EXCEEDED",
                        "replacement expansion exceeds the line budget",
                        {
                            "path": path,
                            "line": line,
                            "query_index": query_index,
                            "max_chars": MAX_REGEX_LINE_CHARS,
                        },
                    )
                output.write(expanded)
                output_chars += len(expanded)
                last_end = match.end()
                count += 1
        except (re.error, IndexError) as exc:
            raise _FileToolError(
                "FILE_REGEX_INVALID",
                "invalid regular expression replacement",
                {"query_index": query_index},
            ) from exc

        tail = text[last_end:]
        if output_chars + len(tail) > MAX_REGEX_LINE_CHARS:
            raise _FileToolError(
                "OUTPUT_LIMIT_EXCEEDED",
                "a transformed text line exceeds the regex processing limit",
                {
                    "path": path,
                    "line": line,
                    "query_index": query_index,
                    "max_chars": MAX_REGEX_LINE_CHARS,
                },
            )
        output.write(tail)
        return output.getvalue(), count

    def read(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        num_lines: Optional[int] = None,
        encoding: Optional[str] = None,
        max_chars: Optional[int] = None,
    ) -> dict[str, Any]:
        action = "read"
        try:
            enc = _validate_encoding(encoding, self.default_encoding)
            start = resolve_int_limit(start_line, START_LINE) if start_line is not None else 1
            count = resolve_int_limit(num_lines, NUM_LINES) if num_lines is not None else None
            char_limit = resolve_int_limit(max_chars, READ_MAX_CHARS)
            snapshot = self._snapshot_text_file(
                file_path,
                encoding=enc,
                require_exists=True,
                for_mutation=False,
            )

            lines = snapshot.text.splitlines(keepends=True)
            if start > len(lines):
                return self._success(
                    action,
                    {
                        "path": snapshot.canonical_path,
                        "content": "",
                        "encoding": enc,
                        "size_bytes": snapshot.size_bytes,
                        "sha256": snapshot.sha256,
                        "is_symlink": snapshot.is_symlink,
                        "start_line": start,
                        "returned_line_count": 0,
                        "next_start_line": None,
                        "eof": True,
                    },
                )

            start_index = start - 1
            requested_end = len(lines) if count is None else min(len(lines), start_index + count)
            selected = lines[start_index:requested_end]

            returned: list[str] = []
            used_chars = 0
            truncated = False
            first_unreturned_line: Optional[int] = None
            for offset, line in enumerate(selected):
                if used_chars + len(line) > char_limit:
                    if not returned:
                        raise _FileToolError(
                            "OUTPUT_LIMIT_EXCEEDED",
                            "one selected line exceeds max_chars and cannot be returned whole",
                            {
                                "path": snapshot.canonical_path,
                                "line": start + offset,
                                "max_chars": char_limit,
                            },
                        )
                    truncated = True
                    first_unreturned_line = start + offset
                    break
                returned.append(line)
                used_chars += len(line)

            if truncated:
                next_start = first_unreturned_line
                eof = False
            else:
                next_index = start_index + len(returned)
                eof = next_index >= len(lines)
                next_start = None if eof else next_index + 1

            return self._success(
                action,
                {
                    "path": snapshot.canonical_path,
                    "content": "".join(returned),
                    "encoding": enc,
                    "size_bytes": snapshot.size_bytes,
                    "sha256": snapshot.sha256,
                    "is_symlink": snapshot.is_symlink,
                    "start_line": start,
                    "returned_line_count": len(returned),
                    "next_start_line": next_start,
                    "eof": eof,
                },
                truncated=truncated,
            )
        except _FileToolError as exc:
            return self._failure(action, exc)
        except Exception as exc:
            if exc.__class__.__module__.startswith("tools.v1._shared"):
                return self._failure(
                    action,
                    _FileToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ),
                )
            raise

    def write(
        self,
        file_path: str,
        content: str,
        mode: str = "w",
        encoding: Optional[str] = None,
    ) -> dict[str, Any]:
        action = "write"
        try:
            if not isinstance(content, str):
                raise _FileToolError("INVALID_ARGUMENT", "content must be a string")
            if len(content) > MAX_WRITE_CONTENT_BYTES:
                raise _FileToolError(
                    "FILE_TOO_LARGE",
                    "write content exceeds the pre-encoding character budget",
                    {"max_chars": MAX_WRITE_CONTENT_BYTES},
                )
            if mode not in ("w", "a"):
                raise _FileToolError("INVALID_ARGUMENT", "mode must be 'w' or 'a'")
            enc = _validate_encoding(encoding, self.default_encoding)
            input_bytes = self._encode_text(content, enc, path="<input>")
            if len(input_bytes) > MAX_WRITE_CONTENT_BYTES:
                raise _FileToolError(
                    "FILE_TOO_LARGE",
                    "write content exceeds the input byte limit",
                    {"max_bytes": MAX_WRITE_CONTENT_BYTES},
                )

            snapshot = self._snapshot_text_file(
                file_path,
                encoding=enc,
                require_exists=False,
                for_mutation=True,
            )
            final_text = content if mode == "w" else snapshot.text + content
            final_bytes = self._encode_text(
                final_text,
                enc,
                path=snapshot.canonical_path,
            )
            changed = (not snapshot.exists) or final_bytes != snapshot.raw_bytes
            after_hash = _sha256(final_bytes)

            if not changed:
                return self._success(
                    action,
                    {
                        "path": snapshot.canonical_path,
                        "mode": mode,
                        "created": False,
                        "changed": False,
                        "input_bytes": len(input_bytes),
                        "final_size_bytes": len(final_bytes),
                        "before_sha256": snapshot.sha256,
                        "after_sha256": snapshot.sha256,
                    },
                )

            if not self._request_confirmation("write", snapshot, final_text):
                raise _FileToolError(
                    "FILE_CHANGE_REJECTED",
                    "file change was rejected by the confirmation callback",
                    {"path": snapshot.canonical_path},
                )

            self._replace_bytes(
                snapshot,
                final_bytes,
                preserve_mode=snapshot.mode if snapshot.exists else None,
            )
            return self._success(
                action,
                {
                    "path": snapshot.canonical_path,
                    "mode": mode,
                    "created": not snapshot.exists,
                    "changed": True,
                    "input_bytes": len(input_bytes),
                    "final_size_bytes": len(final_bytes),
                    "before_sha256": snapshot.sha256,
                    "after_sha256": after_hash,
                },
            )
        except _FileToolError as exc:
            return self._failure(action, exc)

    def search(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> dict[str, Any]:
        action = "search"
        try:
            enc = _validate_encoding(encoding, self.default_encoding)
            paths = self._validate_file_paths(file_paths, require_existing=True)
            query_list, _ = self._validate_queries(queries, is_replace=False)
            patterns = self._compile_patterns(
                query_list,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
            )
            per_file_limit = resolve_int_limit(max_results_per_file, MAX_RESULTS_PER_FILE)

            snapshots = self._snapshot_many(
                paths,
                encoding=enc,
                for_mutation=False,
            )

            total_match_count = 0
            total_returned = 0
            truncated = False
            file_results: list[dict[str, Any]] = []

            for snapshot in snapshots:
                matches: list[dict[str, Any]] = []
                file_match_count = 0
                for line_index, raw_line in enumerate(
                    io.StringIO(snapshot.text),
                    start=1,
                ):
                    line = raw_line.rstrip("\r\n")
                    if len(line) > MAX_REGEX_LINE_CHARS:
                        raise _FileToolError(
                            "OUTPUT_LIMIT_EXCEEDED",
                            "a text line exceeds the regex processing limit",
                            {
                                "path": snapshot.canonical_path,
                                "line": line_index,
                                "max_chars": MAX_REGEX_LINE_CHARS,
                            },
                        )
                    for query_index, pattern in enumerate(patterns):
                        for match in pattern.finditer(line):
                            file_match_count += 1
                            total_match_count += 1
                            if total_match_count > MAX_TOTAL_MATCH_OCCURRENCES:
                                raise _FileToolError(
                                    "OUTPUT_LIMIT_EXCEEDED",
                                    "search occurrence budget exceeded",
                                    {
                                        "max_occurrences": MAX_TOTAL_MATCH_OCCURRENCES,
                                    },
                                )
                            can_report = (
                                len(matches) < per_file_limit
                                and total_returned < MAX_TOTAL_RETURNED_MATCHES
                            )
                            if can_report:
                                snippet, snippet_start, snippet_end = _bounded_line_snippet(
                                    line,
                                    match.start(),
                                    match.end(),
                                )
                                matches.append(
                                    {
                                        "line": line_index,
                                        "query_index": query_index,
                                        "match_start": match.start(),
                                        "match_end": match.end(),
                                        "text": snippet,
                                        "snippet_start": snippet_start,
                                        "snippet_end": snippet_end,
                                    }
                                )
                                total_returned += 1
                            else:
                                truncated = True

                file_results.append(
                    {
                        "path": snapshot.canonical_path,
                        "size_bytes": snapshot.size_bytes,
                        "sha256": snapshot.sha256,
                        "is_symlink": snapshot.is_symlink,
                        "match_count": file_match_count,
                        "returned_count": len(matches),
                        "matches": matches,
                    }
                )

            return self._success(
                action,
                {
                    "files": file_results,
                    "total_match_count": total_match_count,
                    "returned_count": total_returned,
                },
                truncated=truncated,
            )
        except _FileToolError as exc:
            return self._failure(action, exc)
        except Exception as exc:
            if exc.__class__.__module__.startswith("tools.v1._shared"):
                return self._failure(
                    action,
                    _FileToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ),
                )
            raise

    def replace(
        self,
        file_paths: Union[str, List[str]],
        queries: Union[str, List[str]],
        replacements: Union[str, List[str]],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> dict[str, Any]:
        action = "replace"
        try:
            enc = _validate_encoding(encoding, self.default_encoding)
            paths = self._validate_file_paths(file_paths, require_existing=True)
            query_list, replacement_list = self._validate_queries(
                queries,
                replacements,
                is_replace=True,
            )
            assert replacement_list is not None
            patterns = self._compile_patterns(
                query_list,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
            )
            if use_regex:
                for query_index, (pattern, replacement) in enumerate(
                    zip(patterns, replacement_list)
                ):
                    try:
                        # Python's regex engine compiles replacement templates
                        # before scanning the input. An empty subject validates
                        # group references without processing user file content.
                        pattern.sub(replacement, "")
                    except (re.error, IndexError) as exc:
                        raise _FileToolError(
                            "FILE_REGEX_INVALID",
                            "invalid regular expression replacement",
                            {"query_index": query_index},
                        ) from exc
            per_file_limit = resolve_int_limit(max_results_per_file, MAX_RESULTS_PER_FILE)

            snapshots = self._snapshot_many(
                paths,
                encoding=enc,
                for_mutation=True,
            )

            plans: list[_FileMutationPlan] = []
            total_reported = 0
            total_planned_replacements = 0
            report_truncated = False

            for snapshot in snapshots:
                output_text = io.StringIO()
                replacement_count = 0
                records: list[dict[str, Any]] = []
                for line_index, raw_line in enumerate(
                    io.StringIO(snapshot.text),
                    start=1,
                ):
                    newline = ""
                    body = raw_line
                    if raw_line.endswith("\r\n"):
                        body, newline = raw_line[:-2], "\r\n"
                    elif raw_line.endswith("\n") or raw_line.endswith("\r"):
                        body, newline = raw_line[:-1], raw_line[-1:]

                    if len(body) > MAX_REGEX_LINE_CHARS:
                        raise _FileToolError(
                            "OUTPUT_LIMIT_EXCEEDED",
                            "a text line exceeds the regex processing limit",
                            {
                                "path": snapshot.canonical_path,
                                "line": line_index,
                                "max_chars": MAX_REGEX_LINE_CHARS,
                            },
                        )

                    current = body
                    for query_index, pattern in enumerate(patterns):
                        before = current
                        replacement = replacement_list[query_index]
                        remaining_occurrences = (
                            MAX_TOTAL_MATCH_OCCURRENCES - total_planned_replacements
                        )
                        current, count = self._bounded_substitute(
                            pattern,
                            current,
                            replacement,
                            use_regex=use_regex,
                            path=snapshot.canonical_path,
                            line=line_index,
                            query_index=query_index,
                            remaining_occurrences=remaining_occurrences,
                        )
                        if count:
                            replacement_count += count
                            total_planned_replacements += count
                            can_report = (
                                len(records) < per_file_limit
                                and total_reported < MAX_TOTAL_RETURNED_MATCHES
                            )
                            if can_report:
                                before_snippet, _, _ = _bounded_line_snippet(
                                    before,
                                    0,
                                    min(len(before), 1),
                                )
                                after_snippet, _, _ = _bounded_line_snippet(
                                    current,
                                    0,
                                    min(len(current), 1),
                                )
                                records.append(
                                    {
                                        "line": line_index,
                                        "query_index": query_index,
                                        "replacement_count": count,
                                        "before": before_snippet,
                                        "after": after_snippet,
                                    }
                                )
                                total_reported += 1
                            else:
                                report_truncated = True
                    output_text.write(current + newline)

                final_text = output_text.getvalue()
                final_bytes = self._encode_text(
                    final_text,
                    enc,
                    path=snapshot.canonical_path,
                )
                plans.append(
                    _FileMutationPlan(
                        snapshot=snapshot,
                        final_text=final_text,
                        final_bytes=final_bytes,
                        after_sha256=_sha256(final_bytes),
                        replacement_count=replacement_count,
                        change_records=records,
                        reported_change_count=len(records),
                    )
                )

            # Confirmation is completed for every changed plan before the first commit.
            for plan in plans:
                if plan.final_bytes == plan.snapshot.raw_bytes:
                    continue
                if not self._request_confirmation(
                    "replace",
                    plan.snapshot,
                    plan.final_text,
                ):
                    raise _FileToolError(
                        "FILE_CHANGE_REJECTED",
                        "file change was rejected by the confirmation callback",
                        {"path": plan.snapshot.canonical_path},
                    )

            committed: list[_FileMutationPlan] = []
            try:
                for plan in plans:
                    if plan.final_bytes == plan.snapshot.raw_bytes:
                        continue
                    self._replace_bytes(
                        plan.snapshot,
                        plan.final_bytes,
                        preserve_mode=plan.snapshot.mode,
                    )
                    committed.append(plan)
            except _FileToolError as commit_error:
                rolled_back: list[str] = []
                rollback_conflicts: list[str] = []
                rollback_failures: list[str] = []
                for plan in reversed(committed):
                    status_value = self._rollback_committed_plan(plan)
                    if status_value == "rolled_back":
                        rolled_back.append(plan.snapshot.canonical_path)
                    elif status_value == "conflict":
                        rollback_conflicts.append(plan.snapshot.canonical_path)
                    else:
                        rollback_failures.append(plan.snapshot.canonical_path)

                raise _FileToolError(
                    commit_error.code,
                    commit_error.message,
                    {
                        **commit_error.details,
                        "committed_paths": [
                            plan.snapshot.canonical_path for plan in committed
                        ],
                        "rolled_back_paths": rolled_back,
                        "rollback_conflicts": rollback_conflicts,
                        "rollback_failures": rollback_failures,
                    },
                ) from commit_error

            file_results: list[dict[str, Any]] = []
            changed_files = 0
            total_replacements = 0
            for plan in plans:
                changed = plan.final_bytes != plan.snapshot.raw_bytes
                if changed:
                    changed_files += 1
                total_replacements += plan.replacement_count
                file_results.append(
                    {
                        "path": plan.snapshot.canonical_path,
                        "changed": changed,
                        "replacement_count": plan.replacement_count,
                        "reported_change_count": plan.reported_change_count,
                        "before_sha256": plan.snapshot.sha256,
                        "after_sha256": plan.after_sha256,
                        "changes": plan.change_records or [],
                    }
                )

            return self._success(
                action,
                {
                    "files": file_results,
                    "changed_files": changed_files,
                    "total_replacements": total_replacements,
                    "reported_change_count": total_reported,
                },
                truncated=report_truncated,
            )
        except _FileToolError as exc:
            return self._failure(action, exc)
        except Exception as exc:
            if exc.__class__.__module__.startswith("tools.v1._shared"):
                return self._failure(
                    action,
                    _FileToolError(
                        "INVALID_ARGUMENT",
                        str(exc),
                        {"exception_type": type(exc).__name__},
                    ),
                )
            raise

    def execute(
        self,
        action: str,
        file_paths: Optional[Union[str, List[str]]] = None,
        content: Optional[str] = None,
        mode: str = "w",
        queries: Optional[Union[str, List[str]]] = None,
        replacements: Optional[Union[str, List[str]]] = None,
        encoding: Optional[str] = None,
        start_line: Optional[int] = None,
        num_lines: Optional[int] = None,
        max_chars: Optional[int] = None,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_results_per_file: Optional[int] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or action not in {"read", "write", "search", "replace"}:
            return failure_result(
                tool="file_tool",
                action=action if isinstance(action, str) and action else "unknown",
                version=FILE_TOOL_VERSION,
                code="INVALID_ARGUMENT",
                message="unsupported file action",
                details={},
            )

        if file_paths is None:
            file_paths = kwargs.get("file_path")
        if file_paths is None:
            return failure_result(
                tool="file_tool",
                action=action,
                version=FILE_TOOL_VERSION,
                code="INVALID_ARGUMENT",
                message="file_paths is required",
                details={},
            )

        if action in {"read", "write"}:
            try:
                normalized = self._validate_file_paths(
                    file_paths,
                    require_existing=action == "read",
                )
            except _FileToolError as exc:
                return self._failure(action, exc)
            if len(normalized) != 1:
                return failure_result(
                    tool="file_tool",
                    action=action,
                    version=FILE_TOOL_VERSION,
                    code="INVALID_ARGUMENT",
                    message=f"{action} accepts exactly one file path",
                    details={"count": len(normalized)},
                )
            target = normalized[0]
            if action == "read":
                return self.read(
                    target,
                    start_line=start_line,
                    num_lines=num_lines,
                    encoding=encoding,
                    max_chars=max_chars,
                )
            return self.write(
                target,
                content,
                mode=mode,
                encoding=encoding,
            )

        if action == "search":
            return self.search(
                file_paths=file_paths,
                queries=queries,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                max_results_per_file=max_results_per_file,
                encoding=encoding,
            )

        return self.replace(
            file_paths=file_paths,
            queries=queries,
            replacements=replacements,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_results_per_file=max_results_per_file,
            encoding=encoding,
        )


_default_file_tool = FileTool()


def run(
    action: str,
    file_paths: Optional[Union[str, List[str]]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _default_file_tool.execute(
        action=action,
        file_paths=file_paths,
        **kwargs,
    )
