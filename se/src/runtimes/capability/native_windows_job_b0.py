"""Windows Job Object kernel-owned process-tree containment DIAGNOSTIC (SBX3 B0).

No production native command is wired here. Job creation, suspended process
creation, atomic Job assignment DURING CreateProcessW, and kill-on-last-handle-close
are real Windows kernel operations. An experimental observation that the Job
reports zero active processes is NOT OS-isolated sandbox-root cleanup consent,
Windows AppContainer/LPAC, or proof of network NONE.

A standalone Job dies on the owner broker's hard exit (kill on close); it
cannot reattach across worker restart. A durable privileged native service
and generation-authorized receipt broker remain required.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_CREATE_NO_WINDOW = 0x08000000
_INVALID_RESUME = 0xFFFFFFFF


class WindowsJobB0Unavailable(RuntimeError):
    """Windows Job-backed native confinement unavailable: deny before exec."""


@dataclass(frozen=True)
class WindowsJobEmptyObservationB0:
    active_processes: int
    observed: str = "WINDOWS_JOB_EMPTY_DIAGNOSTIC_ONLY"
    production_usable: bool = False
    network_none_certified: bool = False
    durable_reattach_certified: bool = False


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class WindowsJobTreeB0:
    """Single-owner Windows Job: no privileged production broker guarantee.

    The job's process handles never become inheritable. Worker hard death
    closes the last Job handle and the kernel terminates all descendants.
    """

    def __init__(self) -> None:
        if platform.system() != "Windows" or os.name != "nt":
            raise WindowsJobB0Unavailable("Windows Job Object is unsupported here")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
            ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW), ctypes.POINTER(_ProcessInfo),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL
        # Windows 10+/Server: PROC_THREAD_ATTRIBUTE_JOB_LIST is applied
        # atomically during CreateProcessW, not afterwards. A worker crash
        # cannot leave an unassigned suspended child between two syscalls.
        k32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k32.DeleteProcThreadAttributeList.restype = None
        k32.IsProcessInJob.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
        ]
        k32.IsProcessInJob.restype = wintypes.BOOL
        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.c_void_p,
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._kernel = k32
        self._handle = k32.CreateJobObjectW(None, None)
        if not self._handle:
            raise WindowsJobB0Unavailable(
                "CreateJobObjectW failed: " + str(ctypes.get_last_error())
            )
        try:
            limits = _JobExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not k32.SetInformationJobObject(
                self._handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise WindowsJobB0Unavailable(
                    "KILL_ON_JOB_CLOSE setup failed: " + str(ctypes.get_last_error())
                )
        except BaseException:
            k32.CloseHandle(self._handle)
            self._handle = None
            raise

    def __enter__(self) -> "WindowsJobTreeB0":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None

    def spawn_assigned_before_resume(
        self, executable: str | Path, args: Sequence[str] = (),
        *, _on_assigned_for_test: Callable[[int], None] | None = None,
    ) -> int:
        """Atomically CREATE_SUSPENDED *in* kernel Job, then resume thread.

        Never execute before OS-owned membership. On any admission failure
        terminate the suspended process and retain negative result.
        """
        if self._handle is None:
            raise WindowsJobB0Unavailable("closed Job cannot spawn")
        raw = Path(executable)
        if not raw.is_absolute() or any(
            not isinstance(x, str) or "\x00" in x for x in args
        ):
            raise WindowsJobB0Unavailable("trusted absolute executable required")
        try:
            executable_path = raw.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WindowsJobB0Unavailable("executable not resolvable") from exc
        if not executable_path.is_file():
            raise WindowsJobB0Unavailable("trusted executable is not a file")
        argv = [str(executable_path), *args]
        mutable_command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        # Reserve the Windows extended startup attribute list with the current
        # Job handle as an INPUT. Kernel membership happens in CreateProcess
        # itself, before even the child's suspended primary thread exists.
        length = ctypes.c_size_t(0)
        self._kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(length))
        if length.value == 0:
            raise WindowsJobB0Unavailable(
                "native Job attribute size query failed: "
                + str(ctypes.get_last_error())
            )
        storage = ctypes.create_string_buffer(length.value)
        attribute_list = ctypes.cast(storage, ctypes.c_void_p)
        if not self._kernel.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(length)
        ):
            raise WindowsJobB0Unavailable(
                "native Job attribute allocation failed: "
                + str(ctypes.get_last_error())
            )
        try:
            job_handle = wintypes.HANDLE(self._handle)
            if not self._kernel.UpdateProcThreadAttribute(
                attribute_list, 0, _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.byref(job_handle), ctypes.sizeof(job_handle),
                None, None,
            ):
                raise WindowsJobB0Unavailable(
                    "atomic native Job assignment unsupported: "
                    + str(ctypes.get_last_error())
                )
            startup = _StartupInfoExW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = attribute_list
            proc = _ProcessInfo()
            if not self._kernel.CreateProcessW(
                str(executable_path), mutable_command, None, None, False,
                _CREATE_SUSPENDED | _CREATE_NO_WINDOW
                | _EXTENDED_STARTUPINFO_PRESENT,
                None, None,
                ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_StartupInfoW)),
                ctypes.byref(proc),
            ):
                raise WindowsJobB0Unavailable(
                    "atomic Job CreateProcessW denied: "
                    + str(ctypes.get_last_error())
                )
            admitted = False
            try:
                in_job = wintypes.BOOL()
                if not self._kernel.IsProcessInJob(
                    proc.hProcess, self._handle, ctypes.byref(in_job)
                ) or not bool(in_job.value):
                    raise WindowsJobB0Unavailable(
                        "child not atomically enrolled in kernel Job"
                    )
                # A test may hard-exit this broker NOW, before ResumeThread.
                # KILL_ON_JOB_CLOSE kills this already-assigned suspended
                # child instead of leaking a process outside the Job.
                if _on_assigned_for_test is not None:
                    _on_assigned_for_test(int(proc.dwProcessId))
                if self._kernel.ResumeThread(proc.hThread) == _INVALID_RESUME:
                    raise WindowsJobB0Unavailable(
                        "ResumeThread denied: " + str(ctypes.get_last_error())
                    )
                admitted = True
                return int(proc.dwProcessId)
            finally:
                if not admitted:
                    self._kernel.TerminateProcess(proc.hProcess, 91)
                self._kernel.CloseHandle(proc.hThread)
                self._kernel.CloseHandle(proc.hProcess)
        finally:
            self._kernel.DeleteProcThreadAttributeList(attribute_list)

    def active_processes(self) -> int:
        """Read actual kernel Job counters, not /proc, PID lists, or flags."""
        if self._handle is None:
            raise WindowsJobB0Unavailable("closed job cannot prove tree state")
        counters = _JobAccounting()
        if not self._kernel.QueryInformationJobObject(
            self._handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(counters), ctypes.sizeof(counters), None,
        ):
            raise WindowsJobB0Unavailable(
                "Job kernel state unreadable: " + str(ctypes.get_last_error())
            )
        return int(counters.ActiveProcesses)

    def terminate_and_observe_empty(
        self, *, timeout_seconds: float = 10.0
    ) -> WindowsJobEmptyObservationB0:
        """Diagnostic kernel kill + zero active processes. No root authority."""
        if self._handle is None or not 0 < timeout_seconds <= 30:
            raise WindowsJobB0Unavailable("Job handle or timeout invalid")
        if not self._kernel.TerminateJobObject(self._handle, 92):
            raise WindowsJobB0Unavailable(
                "kernel Job tree terminate failed: " + str(ctypes.get_last_error())
            )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.active_processes() == 0:
                if self.active_processes() != 0:
                    continue
                return WindowsJobEmptyObservationB0(0)
            time.sleep(0.03)
        raise WindowsJobB0Unavailable(
            "Job remains populated or kernel cannot prove zero processes"
        )


__all__ = [
    "WindowsJobB0Unavailable", "WindowsJobEmptyObservationB0",
    "WindowsJobTreeB0",
]
