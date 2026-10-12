"""REAL native Windows Job Object B0 security-negative / worker-crash tests.

No Windows AppContainer/LPAC network-NONE or production native sandbox claims.
Ubuntu/Linux must refuse this Windows-only path before any host spawn.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from se.src.runtimes.capability.native_windows_job_b0 import (
    WindowsJobB0Unavailable,
    WindowsJobTreeB0,
)


def _require_windows(test: unittest.TestCase) -> None:
    if platform.system() != "Windows" or os.name != "nt":
        test.skipTest("native Windows kernel Job Objects unavailable")


def _ready(path: Path, *, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.04)
    if not path.exists():
        raise AssertionError("native Job fixture never observed descendant start")


def _windows_pid_active(pid: int) -> bool:
    """Query the real process kernel object (nonexistent == already reaped)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        code = ctypes.get_last_error()
        if code == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        raise AssertionError(f"unknown native process identity {pid}: Win32={code}")
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0: process signaled termination.
            return False
        if status == 0x102:  # WAIT_TIMEOUT: process still alive.
            return True
        raise AssertionError(f"unknown process wait status {pid}: {status}")
    finally:
        kernel.CloseHandle(handle)


class WindowsJobKernelB0Tests(unittest.TestCase):
    def test_unsupported_platform_refuses_before_spawn(self) -> None:
        if platform.system() == "Windows":
            self.skipTest("Linux-specific unsupported host negative")
        with self.assertRaises(WindowsJobB0Unavailable):
            WindowsJobTreeB0()

    def test_windows_cannot_produce_network_none_or_cleanup_certificate(self) -> None:
        _require_windows(self)
        with WindowsJobTreeB0() as job:
            self.assertEqual(job.active_processes(), 0)
            observation = job.terminate_and_observe_empty()
            self.assertEqual(observation.active_processes, 0)
            self.assertFalse(observation.production_usable)
            self.assertFalse(observation.network_none_certified)
            self.assertFalse(observation.durable_reattach_certified)
        with self.assertRaises(WindowsJobB0Unavailable):
            job.active_processes()

    def test_actual_windows_pre_exec_job_membership_before_resume(self) -> None:
        _require_windows(self)
        with tempfile.TemporaryDirectory(prefix="sbx3-job-preexec-") as tmp:
            ready = Path(tmp) / "descendant-started.txt"
            program = (
                "import os,pathlib,time;"
                f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()));"
                "time.sleep(90)"
            )
            observed_preexec: list[int] = []
            with WindowsJobTreeB0() as job:
                def check_suspended(pid: int) -> None:
                    self.assertFalse(
                        ready.exists(), "child ran before Job assignment/ResumeThread"
                    )
                    self.assertGreater(pid, 1)
                    observed_preexec.append(pid)
                    self.assertGreaterEqual(job.active_processes(), 1)

                pid = job.spawn_assigned_before_resume(
                    sys.executable, ("-I", "-c", program),
                    _on_assigned_for_test=check_suspended,
                )
                self.assertEqual(observed_preexec, [pid])
                _ready(ready)
                self.assertEqual(int(ready.read_text()), pid)
                self.assertGreaterEqual(job.active_processes(), 1)
                receipt = job.terminate_and_observe_empty()
                self.assertEqual(receipt.active_processes, 0)
                self.assertFalse(receipt.production_usable)
                self.assertFalse(receipt.network_none_certified)

    def test_actual_windows_worker_hard_exit_kills_child_and_grandchild(self) -> None:
        _require_windows(self)
        with tempfile.TemporaryDirectory(prefix="sbx3-job-hardcrash-") as tmp:
            marker = Path(tmp) / "child-grandchild-pids.txt"
            grandchild_script = "import time;time.sleep(90)"
            child_script = (
                "import os,pathlib,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-I','-c',"
                f"{grandchild_script!r}],"
                "creationflags=subprocess.CREATE_NO_WINDOW)\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "str(os.getpid())+' '+str(child.pid))\n"
                "time.sleep(90)\n"
            )
            supervisor_script = (
                "import os, pathlib, sys, time\n"
                "from se.src.runtimes.capability.native_windows_job_b0 "
                "import WindowsJobTreeB0\n"
                "job=WindowsJobTreeB0()\n"
                "job.spawn_assigned_before_resume(sys.executable, "
                f"('-I', '-c', {child_script!r}))\n"
                f"path=pathlib.Path({str(marker)!r})\n"
                "deadline=time.monotonic()+12\n"
                "while not path.exists() and time.monotonic()<deadline:\n"
                "    time.sleep(.04)\n"
                "if not path.exists():\n"
                "    os._exit(90)\n"
                "os._exit(77)\n"
            )
            worker = subprocess.run(
                [sys.executable, "-c", supervisor_script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=25,
            )
            self.assertEqual(
                worker.returncode, 77,
                worker.stderr.decode("utf-8", "replace")[-1700:],
            )
            _ready(marker)
            child_pid, grandchild_pid = map(int, marker.read_text().split())
            self.assertNotEqual(child_pid, grandchild_pid)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not _windows_pid_active(child_pid) and not _windows_pid_active(grandchild_pid):
                    break
                time.sleep(.08)
            self.assertFalse(
                _windows_pid_active(child_pid),
                "Job owner crash left a child alive",
            )
            self.assertFalse(
                _windows_pid_active(grandchild_pid),
                "Job owner crash left a grandchild alive",
            )

    def test_windows_rejects_relative_path_before_create_process(self) -> None:
        _require_windows(self)
        with WindowsJobTreeB0() as job:
            with self.assertRaises(WindowsJobB0Unavailable):
                job.spawn_assigned_before_resume("python.exe", ("-c", "pass"))
            self.assertEqual(job.active_processes(), 0)


if __name__ == "__main__":
    unittest.main()
