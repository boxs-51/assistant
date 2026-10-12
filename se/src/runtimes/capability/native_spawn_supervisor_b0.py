"""SBX3 B0 diagnostic-only native Linux supervised pre-exec trampoline.

Closes the stopped-child orphan window for a cooperating broker worker:
the trusted trampoline arms PR_SET_PDEATHSIG=SIGKILL before self-stopping.
No untrusted command is executed before the kernel-cgroup enrollment proof,
and a worker hard-crash before that proof kills the pre-exec child.

Not wired to production Agent, Terminal, CRT, AE or root deletion. This does
not protect against malicious same-uid host processes, privileged cgroup
injection, or a broker that bypasses the protocol entirely.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from .native_tree_fence_b0 import NativeSpawnFenceDenied, NativeTreeLifecycleFence

if TYPE_CHECKING:
    from .native_tree_cgroup_b0 import CgroupTreeIdentity


# Fixed, test-owned bootstrap only. All program arguments remain argv,
# never appended to Python source, and -I excludes ambient PYTHONPATH.
_TRUSTED_STOPPED_TRAMPOLINE = r"""
import ctypes
import os
import signal
import sys

parent = int(sys.argv[1])
program = sys.argv[2]
args = sys.argv[2:]
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
    os._exit(125)
if os.getppid() != parent:
    os._exit(126)
os.kill(os.getpid(), signal.SIGSTOP)
# Only after the broker has admitted this pidfd into its exact OS cgroup:
os.execve(program, args, {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
"""


def _await_stopped(child: subprocess.Popen[bytes], *, deadline: float) -> None:
    """Wait for a real kernel stopped state, never an application marker."""
    while time.monotonic() < deadline:
        try:
            pid, status = os.waitpid(child.pid, os.WUNTRACED | os.WNOHANG)
        except (OSError, ChildProcessError) as exc:
            raise NativeSpawnFenceDenied("child state became unavailable") from exc
        if pid == child.pid:
            if os.WIFSTOPPED(status):
                return
            raise NativeSpawnFenceDenied(
                "trusted trampoline exited without pre-exec STOP"
            )
        time.sleep(.01)
    raise NativeSpawnFenceDenied("trusted trampoline failed to stop in time")


def spawn_supervised_stopped_native_b0(
    fence: NativeTreeLifecycleFence,
    identity: "CgroupTreeIdentity",
    executable: str | Path,
    args: Sequence[str] = (),
    *,
    stop_timeout_seconds: float = 8.0,
    _after_stop_for_test: Callable[[int], None] | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn a trusted child with parent-death kill and OS admission gate.

    The parent starts ONLY a fixed Python bootstrap, waits until the kernel
    reports that child SIGSTOPped, attaches it under the cross-process OPEN
    SQL transaction, then resumes via pinned pidfd. On any reported error the
    stopped child is killed and reaped. An abrupt parent process death instead
    triggers PR_SET_PDEATHSIG, including the pre-enrollment stopped interval.

    Note: no positive cleanup production authorization, and no Windows backend.
    """
    if platform.system() != "Linux" or os.name != "posix":
        raise NativeSpawnFenceDenied("OS-native stopped child requires Linux")
    if not 0 < stop_timeout_seconds <= 20:
        raise ValueError("invalid bounded trampoline stop timeout")
    target = Path(executable)
    if (
        not target.is_absolute()
        or not target.is_file()
        or target.is_symlink()
        or not os.access(target, os.X_OK)
        or any(not isinstance(x, str) or "\x00" in x for x in args)
    ):
        raise NativeSpawnFenceDenied("trusted absolute executable and argv required")
    if _after_stop_for_test is not None and not callable(_after_stop_for_test):
        raise TypeError("invalid diagnostic stop hook")
    child = None
    try:
        # The cross-process OPEN lock is acquired BEFORE the first fork/spawn.
        with fence.admit_spawn(identity) as admission:
            child = subprocess.Popen(
                [
                    sys.executable, "-I", "-c", _TRUSTED_STOPPED_TRAMPOLINE,
                    str(os.getpid()), str(target), *args,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            _await_stopped(
                child, deadline=time.monotonic() + stop_timeout_seconds
            )
            if _after_stop_for_test is not None:
                _after_stop_for_test(child.pid)
            admission.admit_stopped_process(child.pid)
            if not admission.admitted:
                raise NativeSpawnFenceDenied(
                    "OS cgroup enrollment absent; never release stopped child"
                )
        return child
    except BaseException:
        if child is not None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass
        raise


__all__ = ["spawn_supervised_stopped_native_b0"]
