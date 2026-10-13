"""SBX3 S1-A explicit OS-owned *transport-only* service.

Linux: run under a dedicated broker UID via an operator/CI service runner.
Windows: --scm enters actual Win32 StartServiceCtrlDispatcher/ServiceMain,
not a console process pretending to be a Windows service.
No CRT registration source exists; every native effect stays DENIED.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import threading
from pathlib import Path

from .native_privileged_broker_s1a import DenyOnlyProtocol, S1AProtocolError
from .native_broker_registration_s1a import ProtectedRegistrationLedger

_STOP = threading.Event()


def _load_config(path: str) -> dict:
    p = Path(path).absolute()
    if p.is_symlink() or not p.is_file():
        raise S1AProtocolError("missing or symlinked broker config")
    if os.name == "posix":
        st = p.stat(follow_symlinks=False)
        if st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise S1AProtocolError("untrusted broker config owner/mode")
    data = json.loads(p.read_text(encoding="utf-8"))
    keys = {"service_name", "state_directory", "linux_socket", "allowed_linux_uid",
            "windows_pipe", "windows_service_sid", "windows_allowed_client_sid"}
    if not isinstance(data, dict) or set(data) != keys:
        raise S1AProtocolError("service configuration schema mismatch")
    if not isinstance(data["service_name"], str) or not data["service_name"].startswith("SBX3S1A"):
        raise S1AProtocolError("invalid dedicated S1-A service name")
    return data


def _serve(config: dict) -> None:
    # Protected state MUST be a broker-only directory, never the untrusted
    # sandbox root. Epoch rotates every real service process restart.
    ledger = ProtectedRegistrationLedger(config["state_directory"])
    proto = DenyOnlyProtocol(ledger.epoch)
    if os.name == "nt":
        from .native_broker_ipc_windows_s1a import WindowsAuthenticatedPipe
        listener = WindowsAuthenticatedPipe(config["windows_pipe"],
            service_sid=config["windows_service_sid"],
            authorized_client_sid=config["windows_allowed_client_sid"], protocol=proto)
    elif os.name == "posix":
        from .native_broker_ipc_linux_s1a import LinuxAuthenticatedSocket
        listener = LinuxAuthenticatedSocket(config["linux_socket"],
            allowed_uid=config["allowed_linux_uid"], protocol=proto)
    else:
        raise S1AProtocolError("native OS service not implemented")
    # Only diagnostic epoch is exposed through a protected, operator-side
    # service process log. No credentials or protected state are logged.
    print(f"SBX3_S1A_TRANSPORT_READY epoch={ledger.epoch} effects=DENY", flush=True)
    listener.serve(_STOP)


def _windows_scm(config: dict) -> int:
    if os.name != "nt":
        raise S1AProtocolError("Windows SCM dispatcher requires native Windows")
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    SERVICE_WIN32_OWN_PROCESS = 0x10
    SERVICE_STOPPED, SERVICE_START_PENDING, SERVICE_STOP_PENDING, SERVICE_RUNNING = 1, 2, 3, 4
    SERVICE_ACCEPT_STOP, SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN = 1, 1, 5

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [("dwServiceType", w.DWORD), ("dwCurrentState", w.DWORD),
                    ("dwControlsAccepted", w.DWORD), ("dwWin32ExitCode", w.DWORD),
                    ("dwServiceSpecificExitCode", w.DWORD), ("dwCheckPoint", w.DWORD),
                    ("dwWaitHint", w.DWORD)]

    MAIN = ctypes.WINFUNCTYPE(None, w.DWORD, ctypes.POINTER(w.LPWSTR))
    HANDLER = ctypes.WINFUNCTYPE(w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, ctypes.c_void_p)

    class ENTRY(ctypes.Structure):
        _fields_ = [("lpServiceName", w.LPWSTR), ("lpServiceProc", MAIN)]

    state = {"status_handle": None, "failure": False}

    def set_status(current: int, *, accept: int = 0, error: int = 0) -> None:
        status = SERVICE_STATUS(SERVICE_WIN32_OWN_PROCESS, current, accept, error, 0, 0, 2000)
        if not adv.SetServiceStatus(state["status_handle"], ctypes.byref(status)):
            raise S1AProtocolError("SCM status publication failed")

    @HANDLER
    def control(op, event_type, event_data, context):
        if op in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            _STOP.set()
            try:
                set_status(SERVICE_STOP_PENDING)
            except Exception:
                state["failure"] = True
            # Server ConnectNamedPipe/ReadFile now have bounded overlapped
            # deadlines. Never block the SCM control callback on client IPC.
        return 0

    @MAIN
    def service_main(argc, argv):
        state["status_handle"] = adv.RegisterServiceCtrlHandlerExW(
            w.LPCWSTR(config["service_name"]), control, None)
        if not state["status_handle"]:
            state["failure"] = True
            return
        try:
            set_status(SERVICE_START_PENDING)
            # StartServiceCtrlDispatcher guarantees this code runs on SCM
            # service dispatch, not under a synthetic Start-Process worker.
            set_status(SERVICE_RUNNING, accept=SERVICE_ACCEPT_STOP)
            _serve(config)
        except BaseException:
            state["failure"] = True
        finally:
            set_status(SERVICE_STOPPED, error=1 if state["failure"] else 0)

    adv.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
    adv.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
    adv.SetServiceStatus.restype = w.BOOL
    adv.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(ENTRY)]
    adv.StartServiceCtrlDispatcherW.restype = w.BOOL
    table = (ENTRY * 2)()
    table[0] = ENTRY(config["service_name"], service_main)
    table[1] = ENTRY(None, MAIN())
    if not adv.StartServiceCtrlDispatcherW(table):
        raise S1AProtocolError("not started by real Windows SCM dispatcher")
    return 1 if state["failure"] else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="operator-provisioned broker-owned JSON path")
    p.add_argument("--scm", action="store_true", help="native Windows ServiceMain only")
    args = p.parse_args()
    config = _load_config(args.config)
    if os.name == "nt":
        if not args.scm:
            raise S1AProtocolError("Windows S1-A must run under real SCM")
        return _windows_scm(config)
    if args.scm:
        raise S1AProtocolError("SCM mode not supported on Linux")
    signal.signal(signal.SIGTERM, lambda _sig, _frame: _STOP.set())
    signal.signal(signal.SIGINT, lambda _sig, _frame: _STOP.set())
    _serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
