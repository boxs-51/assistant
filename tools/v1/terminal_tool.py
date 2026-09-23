from __future__ import annotations

import codecs
import locale
import os
import signal
import subprocess
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import psutil

from tools.v1._shared.contracts import failure_result, success_result
from tools.v1._shared.limits import IntLimitSpec, resolve_int_limit


TERMINAL_TOOL_VERSION = "2.0.0"

MAX_COMMAND_CHARS = 32_768
MAX_CWD_CHARS = 4_096
MAX_ENCODING_CHARS = 64

RUN_TIMEOUT = IntLimitSpec(
    "timeout",
    default=30,
    minimum=1,
    maximum=3_600,
)

MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 4 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024

TERMINATION_GRACE_SECONDS = 2.0
TERMINATION_GRACE_HARD_MAX_SECONDS = 10.0

PIPE_READ_CHUNK_BYTES = 64 * 1024
PROCESS_POLL_INTERVAL_SECONDS = 0.05
PIPE_JOIN_GRACE_SECONDS = 1.0
MAX_REPORTED_SURVIVOR_PIDS = 64

DEFAULT_DANGER_PATTERNS = (
    r"rm\s+-rf",
    r"mkfs",
    r"format\s+[a-z]:",
    r"shutdown",
    r"reboot",
    r":\(\)\{\s*:\|:&\s*\};:",
)


TOOL_METADATA = {
    "name": "terminal_tool",
    "description": (
        "Thực thi shell command dạng có quản lý bằng action='run' với stdout/stderr "
        "có giới hạn, timeout và cleanup process tree; hoặc action='launch' để khởi "
        "chạy tiến trình tách rời và trả PID process leader. Shell execution có rủi ro cao."
    ),
    "base_risk": "HIGH",
    "effects": ["EXECUTE", "EXTERNAL_SIDE_EFFECT"],
    "danger_patterns": list(DEFAULT_DANGER_PATTERNS),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "launch"],
            },
            "command": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_COMMAND_CHARS,
            },
            "timeout": {
                "type": "integer",
                "minimum": RUN_TIMEOUT.minimum,
                "maximum": RUN_TIMEOUT.maximum,
                "description": "Chỉ dùng cho action='run'.",
            },
            "cwd": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_CWD_CHARS,
            },
            "encoding": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_ENCODING_CHARS,
                "description": "Codec dùng để decode stdout/stderr của action='run'.",
            },
        },
        "required": ["action", "command"],
    },
}


class _TerminalToolError(Exception):
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
class _ProcessIdentity:
    pid: int
    create_time: float


@dataclass
class _CleanupReport:
    attempted: bool
    graceful_pids: list[int] = field(default_factory=list)
    killed_pids: list[int] = field(default_factory=list)
    survivor_pids: list[int] = field(default_factory=list)
    verified: bool = True


@dataclass
class _OutputCaptureState:
    stdout_buffer: bytearray = field(default_factory=bytearray)
    stderr_buffer: bytearray = field(default_factory=bytearray)
    stdout_bytes_observed: int = 0
    stderr_bytes_observed: int = 0
    overflow_stream: Optional[str] = None
    io_error_stream: Optional[str] = None
    io_error_type: Optional[str] = None
    signal_event: threading.Event = field(default_factory=threading.Event)
    cleanup_started: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


_PSUTIL_GONE = (psutil.NoSuchProcess, psutil.ZombieProcess)
_PSUTIL_EXPECTED = (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied)


def _validate_command(command: Any) -> str:
    if not isinstance(command, str) or not command.strip():
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            "command must be a non-empty string",
        )
    if "\x00" in command:
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            "command contains a NUL character",
        )
    if len(command) > MAX_COMMAND_CHARS:
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            f"command exceeds the hard limit of {MAX_COMMAND_CHARS} characters",
        )
    return command


def _validate_cwd(cwd: Optional[str]) -> str:
    if cwd is None:
        return str(Path.cwd().resolve())

    if not isinstance(cwd, str) or not cwd:
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            "cwd must be a non-empty string when provided",
        )
    if "\x00" in cwd:
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            "cwd contains a NUL character",
        )
    if len(cwd) > MAX_CWD_CHARS:
        raise _TerminalToolError(
            "INVALID_ARGUMENT",
            f"cwd exceeds the hard limit of {MAX_CWD_CHARS} characters",
        )

    path = Path(cwd)
    try:
        if not path.exists():
            raise _TerminalToolError(
                "TERMINAL_CWD_NOT_FOUND",
                "terminal working directory does not exist",
                {"cwd": str(path.absolute())},
            )
        if not path.is_dir():
            raise _TerminalToolError(
                "TERMINAL_CWD_NOT_DIRECTORY",
                "terminal working path is not a directory",
                {"cwd": str(path.absolute())},
            )
        return str(path.resolve())
    except _TerminalToolError:
        raise
    except OSError as exc:
        raise _TerminalToolError(
            "TERMINAL_IO_ERROR",
            "terminal working directory could not be inspected",
            {
                "exception_type": type(exc).__name__,
                "errno": getattr(exc, "errno", None),
            },
        ) from exc


def _validate_encoding(encoding: Optional[str]) -> str:
    value = locale.getpreferredencoding(False) if encoding is None else encoding
    if not isinstance(value, str) or not value:
        raise _TerminalToolError(
            "TERMINAL_ENCODING_INVALID",
            "terminal output encoding must be a non-empty string",
        )
    if len(value) > MAX_ENCODING_CHARS:
        raise _TerminalToolError(
            "TERMINAL_ENCODING_INVALID",
            "terminal output encoding name is too long",
        )
    try:
        return codecs.lookup(value).name
    except LookupError as exc:
        raise _TerminalToolError(
            "TERMINAL_ENCODING_INVALID",
            "unknown terminal output encoding",
        ) from exc


def _resolve_timeout(timeout: Optional[int], default_timeout: int) -> int:
    value = default_timeout if timeout is None else timeout
    try:
        return resolve_int_limit(value, RUN_TIMEOUT)
    except Exception as exc:
        if exc.__class__.__module__.startswith("tools.v1._shared"):
            raise _TerminalToolError(
                "INVALID_ARGUMENT",
                str(exc),
                {"exception_type": type(exc).__name__},
            ) from exc
        raise


def _platform_run_popen_kwargs(cwd: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "shell": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
        "bufsize": 0,
        "cwd": cwd,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _platform_launch_popen_kwargs(cwd: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "shell": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": cwd,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _capture_identity(pid: int) -> Optional[_ProcessIdentity]:
    try:
        process = psutil.Process(pid)
        return _ProcessIdentity(pid=pid, create_time=process.create_time())
    except _PSUTIL_EXPECTED:
        return None


def _identity_matches(identity: _ProcessIdentity) -> bool:
    try:
        process = psutil.Process(identity.pid)
    except _PSUTIL_GONE:
        return False

    try:
        if abs(process.create_time() - identity.create_time) >= 0.001:
            return False
        return process.status() != psutil.STATUS_ZOMBIE
    except _PSUTIL_GONE:
        return False
    except psutil.AccessDenied:
        # Verification must fail closed. We know the PID still exists but
        # cannot prove it is gone or inspect its terminal state.
        return True


def _matching_process(identity: _ProcessIdentity) -> Optional[psutil.Process]:
    try:
        process = psutil.Process(identity.pid)
    except _PSUTIL_GONE:
        return None

    try:
        if abs(process.create_time() - identity.create_time) >= 0.001:
            return None
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except _PSUTIL_GONE:
        return None
    except psutil.AccessDenied:
        return None


def _observe_descendants(
    root_pid: int,
    identities: dict[int, _ProcessIdentity],
) -> None:
    try:
        root = psutil.Process(root_pid)
        children = root.children(recursive=True)
    except _PSUTIL_EXPECTED:
        return

    for child in children:
        try:
            identity = _ProcessIdentity(
                pid=child.pid,
                create_time=child.create_time(),
            )
        except _PSUTIL_EXPECTED:
            continue
        identities.setdefault(identity.pid, identity)


def _posix_group_exists(pgid: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _record_output_chunk(
    state: _OutputCaptureState,
    stream_name: str,
    chunk: bytes,
) -> None:
    with state.lock:
        if stream_name == "stdout":
            buffer = state.stdout_buffer
            state.stdout_bytes_observed += len(chunk)
            stream_observed = state.stdout_bytes_observed
            stream_limit = MAX_STDOUT_BYTES
        else:
            buffer = state.stderr_buffer
            state.stderr_bytes_observed += len(chunk)
            stream_observed = state.stderr_bytes_observed
            stream_limit = MAX_STDERR_BYTES

        total_observed = state.stdout_bytes_observed + state.stderr_bytes_observed
        retained_total = len(state.stdout_buffer) + len(state.stderr_buffer)

        stream_remaining = max(0, stream_limit - len(buffer))
        total_remaining = max(0, MAX_TOTAL_OUTPUT_BYTES - retained_total)
        keep = min(len(chunk), stream_remaining, total_remaining)
        if keep:
            buffer.extend(chunk[:keep])

        if state.overflow_stream is None:
            if stream_observed > stream_limit:
                state.overflow_stream = stream_name
            elif total_observed > MAX_TOTAL_OUTPUT_BYTES:
                state.overflow_stream = "aggregate"

        if state.overflow_stream is not None:
            state.signal_event.set()


def _reader_loop(
    pipe: Any,
    stream_name: str,
    state: _OutputCaptureState,
) -> None:
    try:
        while True:
            chunk = pipe.read(PIPE_READ_CHUNK_BYTES)
            if not chunk:
                break
            _record_output_chunk(state, stream_name, chunk)
    except OSError as exc:
        if not state.cleanup_started.is_set():
            with state.lock:
                if state.io_error_stream is None:
                    state.io_error_stream = stream_name
                    state.io_error_type = type(exc).__name__
                    state.signal_event.set()


def _close_pipe(pipe: Any) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        pass


def _join_readers(
    threads: list[threading.Thread],
    timeout: float = PIPE_JOIN_GRACE_SECONDS,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    for thread in threads:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
    return not any(thread.is_alive() for thread in threads)


def _owned_processes(
    identities: dict[int, _ProcessIdentity],
) -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for identity in identities.values():
        process = _matching_process(identity)
        if process is not None:
            processes.append(process)
    return processes


def _send_posix_group_signal(pgid: int, sig: int) -> None:
    if os.name == "nt":
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _terminate_process_tree(
    process: subprocess.Popen[Any],
    identities: dict[int, _ProcessIdentity],
    *,
    trigger: str,
) -> _CleanupReport:
    del trigger  # trigger is part of the caller-visible error, not cleanup mechanics.
    report = _CleanupReport(attempted=True)

    _observe_descendants(process.pid, identities)
    candidates = _owned_processes(identities)

    if os.name != "nt":
        _send_posix_group_signal(process.pid, signal.SIGTERM)
    else:
        ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break is not None and process.poll() is None:
            try:
                process.send_signal(ctrl_break)
            except (OSError, ValueError):
                pass

    for candidate in candidates:
        try:
            candidate.terminate()
            report.graceful_pids.append(candidate.pid)
        except _PSUTIL_EXPECTED:
            continue

    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass

    if candidates:
        try:
            psutil.wait_procs(candidates, timeout=TERMINATION_GRACE_SECONDS)
        except (ValueError, OSError):
            pass

    grace_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < grace_deadline:
        still_owned = any(_identity_matches(identity) for identity in identities.values())
        group_alive = _posix_group_exists(process.pid)
        if not still_owned and not group_alive and process.poll() is not None:
            break
        time.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, max(0.0, grace_deadline - time.monotonic())))

    _observe_descendants(process.pid, identities)
    remaining = _owned_processes(identities)
    group_alive = _posix_group_exists(process.pid)

    if os.name != "nt" and group_alive:
        _send_posix_group_signal(process.pid, signal.SIGKILL)

    for candidate in remaining:
        try:
            candidate.kill()
            report.killed_pids.append(candidate.pid)
        except _PSUTIL_EXPECTED:
            continue

    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass

    if remaining:
        try:
            psutil.wait_procs(remaining, timeout=TERMINATION_GRACE_SECONDS)
        except (ValueError, OSError):
            pass

    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        pass

    survivors: list[int] = []
    for identity in identities.values():
        if _identity_matches(identity):
            survivors.append(identity.pid)
            if len(survivors) >= MAX_REPORTED_SURVIVOR_PIDS:
                break

    if os.name != "nt" and _posix_group_exists(process.pid):
        if process.pid not in survivors and len(survivors) < MAX_REPORTED_SURVIVOR_PIDS:
            survivors.append(process.pid)

    report.survivor_pids = sorted(set(survivors))
    report.verified = not report.survivor_pids
    return report


def _cleanup_failure(
    *,
    trigger: str,
    report: _CleanupReport,
) -> _TerminalToolError:
    return _TerminalToolError(
        "TERMINAL_CLEANUP_FAILED",
        "terminal process tree cleanup could not be verified",
        {
            "trigger": trigger,
            "survivor_pids": report.survivor_pids[:MAX_REPORTED_SURVIVOR_PIDS],
            "survivor_count": len(report.survivor_pids),
        },
    )


class TerminalTool:
    """Bounded shell command execution and detached launch."""

    def __init__(
        self,
        default_timeout: int = RUN_TIMEOUT.default,
        confirm_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    ) -> None:
        self.default_timeout = _resolve_timeout(default_timeout, RUN_TIMEOUT.default)
        if confirm_callback is not None:
            warnings.warn(
                "confirm_callback is deprecated and is not used by TerminalTool; "
                "authorization/HITL belongs to the consumer policy layer",
                DeprecationWarning,
                stacklevel=2,
            )

    def _success(
        self,
        action: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return success_result(
            tool="terminal_tool",
            action=action,
            version=TERMINAL_TOOL_VERSION,
            data=data,
        )

    def _failure(
        self,
        action: str,
        error: _TerminalToolError,
    ) -> dict[str, Any]:
        return failure_result(
            tool="terminal_tool",
            action=action,
            version=TERMINAL_TOOL_VERSION,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    def _validate_cwd(self, cwd: Optional[str]) -> str:
        return _validate_cwd(cwd)

    def _run_managed_process(
        self,
        *,
        command: str,
        timeout: int,
        cwd: str,
        encoding: str,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                **_platform_run_popen_kwargs(cwd),
            )
        except OSError as exc:
            raise _TerminalToolError(
                "TERMINAL_START_FAILED",
                "terminal command could not be started",
                {
                    "cwd": cwd,
                    "errno": getattr(exc, "errno", None),
                    "winerror": getattr(exc, "winerror", None),
                    "exception_type": type(exc).__name__,
                },
            ) from exc

        state = _OutputCaptureState()
        identities: dict[int, _ProcessIdentity] = {}
        threads: list[threading.Thread] = []
        cleanup_completed = False

        try:
            root_identity = _capture_identity(process.pid)
            if root_identity is not None:
                identities[root_identity.pid] = root_identity
            _observe_descendants(process.pid, identities)

            stdout_thread = threading.Thread(
                target=_reader_loop,
                args=(process.stdout, "stdout", state),
                name=f"terminal-stdout-{process.pid}",
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_reader_loop,
                args=(process.stderr, "stderr", state),
                name=f"terminal-stderr-{process.pid}",
                daemon=True,
            )
            threads = [stdout_thread, stderr_thread]
            for thread in threads:
                thread.start()

            deadline = started_at + timeout
            trigger = "normal_exit"

            while True:
                _observe_descendants(process.pid, identities)

                if state.overflow_stream is not None:
                    trigger = "output_limit"
                    break
                if state.io_error_stream is not None:
                    trigger = "io_error"
                    break

                if process.poll() is not None:
                    trigger = "normal_exit"
                    break

                if time.monotonic() >= deadline:
                    trigger = "timeout"
                    break

                remaining = max(0.0, deadline - time.monotonic())
                state.signal_event.wait(min(PROCESS_POLL_INTERVAL_SECONDS, remaining))
                state.signal_event.clear()

            if trigger == "normal_exit":
                # Give finite output a bounded chance to reach EOF. A background
                # descendant inheriting the pipes keeps readers alive and is then
                # treated as part of the managed run tree.
                readers_done = _join_readers(threads, PIPE_JOIN_GRACE_SECONDS)
                _observe_descendants(process.pid, identities)
                owned_alive = any(
                    _identity_matches(identity) for identity in identities.values()
                )
                group_alive = _posix_group_exists(process.pid)

                if not readers_done or owned_alive or group_alive:
                    state.cleanup_started.set()
                    report = _terminate_process_tree(
                        process,
                        identities,
                        trigger="post_exit_descendant",
                    )
                    cleanup_completed = True
                    _close_pipe(process.stdout)
                    _close_pipe(process.stderr)
                    readers_done = _join_readers(threads, PIPE_JOIN_GRACE_SECONDS)
                    if not report.verified or not readers_done:
                        if not report.verified:
                            raise _cleanup_failure(
                                trigger="post_exit_descendant",
                                report=report,
                            )
                        raise _TerminalToolError(
                            "TERMINAL_CLEANUP_FAILED",
                            "terminal output readers did not stop after process cleanup",
                            {
                                "trigger": "post_exit_descendant",
                                "survivor_pids": [],
                                "survivor_count": 0,
                            },
                        )

                if state.overflow_stream is not None:
                    trigger = "output_limit"
                elif state.io_error_stream is not None:
                    trigger = "io_error"
                else:
                    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
                    stdout = bytes(state.stdout_buffer).decode(encoding, errors="replace")
                    stderr = bytes(state.stderr_buffer).decode(encoding, errors="replace")
                    return {
                        "exit_code": int(process.returncode),
                        "stdout": stdout,
                        "stderr": stderr,
                        "stdout_bytes": state.stdout_bytes_observed,
                        "stderr_bytes": state.stderr_bytes_observed,
                        "encoding": encoding,
                        "duration_ms": duration_ms,
                        "cwd": cwd,
                    }

            state.cleanup_started.set()
            report = _terminate_process_tree(
                process,
                identities,
                trigger=trigger,
            )
            cleanup_completed = True
            _close_pipe(process.stdout)
            _close_pipe(process.stderr)
            readers_done = _join_readers(threads, PIPE_JOIN_GRACE_SECONDS)

            if not report.verified or not readers_done:
                if not report.verified:
                    raise _cleanup_failure(trigger=trigger, report=report)
                raise _TerminalToolError(
                    "TERMINAL_CLEANUP_FAILED",
                    "terminal output readers did not stop after process cleanup",
                    {
                        "trigger": trigger,
                        "survivor_pids": [],
                        "survivor_count": 0,
                    },
                )

            duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
            if trigger == "timeout":
                raise _TerminalToolError(
                    "TERMINAL_TIMEOUT",
                    "terminal command exceeded the execution timeout",
                    {
                        "timeout_seconds": timeout,
                        "duration_ms": duration_ms,
                    },
                )
            if trigger == "output_limit":
                raise _TerminalToolError(
                    "TERMINAL_OUTPUT_LIMIT",
                    "terminal command exceeded the output byte limit",
                    {
                        "overflow_stream": state.overflow_stream,
                        "stdout_bytes_observed": state.stdout_bytes_observed,
                        "stderr_bytes_observed": state.stderr_bytes_observed,
                        "max_stdout_bytes": MAX_STDOUT_BYTES,
                        "max_stderr_bytes": MAX_STDERR_BYTES,
                        "max_total_output_bytes": MAX_TOTAL_OUTPUT_BYTES,
                        "duration_ms": duration_ms,
                    },
                )
            raise _TerminalToolError(
                "TERMINAL_IO_ERROR",
                "terminal output capture failed",
                {
                    "stream": state.io_error_stream,
                    "exception_type": state.io_error_type,
                    "duration_ms": duration_ms,
                },
            )

        except BaseException:
            if not cleanup_completed:
                state.cleanup_started.set()
                try:
                    _terminate_process_tree(
                        process,
                        identities,
                        trigger="base_exception",
                    )
                except BaseException:
                    # Cleanup must never mask the original control-flow or
                    # programmer exception.
                    pass
            _close_pipe(process.stdout)
            _close_pipe(process.stderr)
            _join_readers(threads, PIPE_JOIN_GRACE_SECONDS)
            raise
        finally:
            _close_pipe(process.stdout)
            _close_pipe(process.stderr)

    def run(
        self,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        encoding: Optional[str] = None,
    ) -> dict[str, Any]:
        action = "run"
        try:
            validated_command = _validate_command(command)
            validated_cwd = _validate_cwd(cwd)
            validated_timeout = _resolve_timeout(timeout, self.default_timeout)
            validated_encoding = _validate_encoding(encoding)
            data = self._run_managed_process(
                command=validated_command,
                timeout=validated_timeout,
                cwd=validated_cwd,
                encoding=validated_encoding,
            )
            return self._success(action, data)
        except _TerminalToolError as exc:
            return self._failure(action, exc)

    def launch(
        self,
        command: str,
        cwd: Optional[str] = None,
    ) -> dict[str, Any]:
        action = "launch"
        try:
            validated_command = _validate_command(command)
            validated_cwd = _validate_cwd(cwd)
            try:
                process = subprocess.Popen(
                    validated_command,
                    **_platform_launch_popen_kwargs(validated_cwd),
                )
            except OSError as exc:
                raise _TerminalToolError(
                    "TERMINAL_START_FAILED",
                    "terminal launch could not be started",
                    {
                        "cwd": validated_cwd,
                        "errno": getattr(exc, "errno", None),
                        "winerror": getattr(exc, "winerror", None),
                        "exception_type": type(exc).__name__,
                    },
                ) from exc

            if type(process.pid) is not int or process.pid <= 0:
                raise _TerminalToolError(
                    "TERMINAL_START_FAILED",
                    "terminal launch returned an invalid process identifier",
                    {"cwd": validated_cwd},
                )

            return self._success(
                action,
                {
                    "pid": process.pid,
                    "cwd": validated_cwd,
                    "started": True,
                },
            )
        except _TerminalToolError as exc:
            return self._failure(action, exc)

    def execute(
        self,
        action: str,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        encoding: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        if action == "run":
            return self.run(
                command=command,
                timeout=timeout,
                cwd=cwd,
                encoding=encoding,
            )
        if action == "launch":
            return self.launch(
                command=command,
                cwd=cwd,
            )
        return failure_result(
            tool="terminal_tool",
            action=action if isinstance(action, str) and action else "unknown",
            version=TERMINAL_TOOL_VERSION,
            code="INVALID_ARGUMENT",
            message="unsupported terminal action",
            details={},
        )


def run(
    action: str,
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    encoding: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    tool = TerminalTool()
    return tool.execute(
        action=action,
        command=command,
        timeout=timeout,
        cwd=cwd,
        encoding=encoding,
        **kwargs,
    )
