"""Native S1-A local named-pipe transport with time-bounded overlapped Win32 I/O.

No host Popen, model tools, native effects, or positive receipt authority.
All connections use kernel impersonation and a restricted service/client SID ACL.
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import threading
import time
from ctypes import wintypes as w
from typing import Callable

from .native_privileged_broker_s1a import (
    MAX_FRAME, DenyOnlyProtocol, S1AProtocolError, decode_frame, encode_frame,
)

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0
PIPE_READMODE_BYTE = 0
PIPE_WAIT = 0
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_CREATE_PIPE_INSTANCE = 0x0004
SYNCHRONIZE = 0x00100000
READ_CONTROL = 0x00020000
# Client may read/write data, but MUST NOT create a pipe instance.
CLIENT_PIPE_RIGHTS = 0x00120183
CLIENT_OPEN_RIGHTS = FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE
OPEN_EXISTING = 3
TOKEN_QUERY = 0x0008
ERROR_PIPE_CONNECTED = 535
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INVALID = ctypes.c_void_p(-1).value
SID_RE = re.compile(r"S-1-(?:[0-9]+-)*[0-9]+\Z")
PIPE_PREFIX = "\\\\.\\pipe\\SBX3S1A-"

# Whole connection must complete or fail closed; slow-byte client cannot
# extend this timeout indefinitely.
CONNECT_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 2.0
CLIENT_TIMEOUT_S = 4.0
CANCEL_DRAIN_TIMEOUT_MS = 1000
# The server MUST wait for an ACK written only after the client decoded its
# full DENY frame. WriteFile completion alone does not mean client consumption.
# This byte never denotes execution authorization, receipt, or a success status.
TRANSPORT_FRAME_CONSUMED_ACK = b"\x06"


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", w.BOOL)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


def _winlibs():
    if os.name != "nt":
        raise S1AProtocolError("native Windows kernel pipe required")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    k.CreateNamedPipeW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD,
                                   w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES)]
    k.CreateNamedPipeW.restype = ctypes.c_void_p
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                              w.DWORD, w.DWORD, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
    k.CreateEventW.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle.restype = w.BOOL
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    k.WaitNamedPipeW.argtypes = [w.LPCWSTR, w.DWORD]
    k.WaitNamedPipeW.restype = w.BOOL
    k.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]
    k.ConnectNamedPipe.restype = w.BOOL
    k.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]
    k.CancelIoEx.restype = w.BOOL
    k.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED),
                                      ctypes.POINTER(w.DWORD), w.BOOL]
    k.GetOverlappedResult.restype = w.BOOL
    k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.DWORD,
                            ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
    k.ReadFile.restype = w.BOOL
    k.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.DWORD,
                             ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
    k.WriteFile.restype = w.BOOL
    k.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
    k.DisconnectNamedPipe.restype = w.BOOL
    k.GetCurrentThread.restype = ctypes.c_void_p
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    a.ImpersonateNamedPipeClient.argtypes = [ctypes.c_void_p]
    a.ImpersonateNamedPipeClient.restype = w.BOOL
    a.OpenThreadToken.argtypes = [ctypes.c_void_p, w.DWORD, w.BOOL,
                                  ctypes.POINTER(ctypes.c_void_p)]
    a.OpenThreadToken.restype = w.BOOL
    a.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                      w.DWORD, ctypes.POINTER(w.DWORD)]
    a.GetTokenInformation.restype = w.BOOL
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertSidToStringSidW.restype = w.BOOL
    a.RevertToSelf.argtypes = []
    a.RevertToSelf.restype = w.BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    return k, a


def _validate_pipe(name: str) -> None:
    if (not isinstance(name, str) or not name.startswith(PIPE_PREFIX)
            or len(name) > 180 or len(name) <= len(PIPE_PREFIX)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", name[len(PIPE_PREFIX):])):
        raise S1AProtocolError("invalid local private pipe name")


def _overlapped(k, handle: int, begin: Callable[[OVERLAPPED], bool],
                deadline: float, *, already_connected_ok: bool = False,
                stop: threading.Event | None = None) -> int:
    """Wait for completion; cancel and DRAIN before buffers/event can be freed.

    If the kernel cannot drain a cancelled request, terminate the *deny-only*
    broker process rather than free Python buffers still owned by kernel I/O.
    No native effects are ever started by this process.
    """
    event = k.CreateEventW(None, True, False, None)
    if not event:
        raise S1AProtocolError("cannot create kernel event")
    ov = OVERLAPPED()
    ov.hEvent = event
    try:
        success = bool(begin(ov))
        if not success:
            error = ctypes.get_last_error()
            if already_connected_ok and error == ERROR_PIPE_CONNECTED:
                return 0
            if error != ERROR_IO_PENDING:
                raise S1AProtocolError("native pipe operation rejected")
        if not success:
            completed = False
            while True:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if (stop is not None and stop.is_set()) or not remaining_ms:
                    break
                # A pending listener stays stable while SCM STOP is observed
                # within one polling slice; no 500ms pipe-instance churn.
                wait_ms = min(100, remaining_ms) if stop is not None else remaining_ms
                result = k.WaitForSingleObject(event, wait_ms)
                if result == WAIT_OBJECT_0:
                    completed = True
                    break
                if result != WAIT_TIMEOUT:
                    break
            if not completed:
                k.CancelIoEx(ctypes.c_void_p(handle), ctypes.byref(ov))
                # Cancellation is asynchronous. Do not release event/OVERLAPPED
                # storage until the kernel reports completion/cancellation.
                drained = k.WaitForSingleObject(event, CANCEL_DRAIN_TIMEOUT_MS)
                if drained != WAIT_OBJECT_0:
                    os._exit(86)  # safety terminal; no UAF, no native effects
                transferred_after_cancel = w.DWORD()
                # ERROR_OPERATION_ABORTED and successful completion races are
                # both terminal; the storage is freed only after kernel signal.
                k.GetOverlappedResult(ctypes.c_void_p(handle), ctypes.byref(ov),
                                      ctypes.byref(transferred_after_cancel), False)
                raise S1AProtocolError("bounded/cancelled Windows pipe request")
        transferred = w.DWORD()
        if not k.GetOverlappedResult(ctypes.c_void_p(handle), ctypes.byref(ov),
                                      ctypes.byref(transferred), False):
            raise S1AProtocolError("native overlapped result unavailable")
        return transferred.value
    finally:
        k.CloseHandle(ctypes.c_void_p(event))


def _read_exact(k, handle: int, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        if time.monotonic() >= deadline:
            raise S1AProtocolError("bounded read deadline exceeded")
        request = min(size - len(chunks), 4096)
        buffer = ctypes.create_string_buffer(request)
        def begin(ov: OVERLAPPED) -> bool:
            return bool(k.ReadFile(ctypes.c_void_p(handle), buffer, request,
                                   None, ctypes.byref(ov)))
        completed = _overlapped(k, handle, begin, deadline)
        if not completed or completed > request:
            raise S1AProtocolError("truncated native pipe data")
        chunks.extend(buffer.raw[:completed])
    return bytes(chunks)


def _receive(k, handle: int, deadline: float) -> dict:
    header = _read_exact(k, handle, 4, deadline)
    length = struct.unpack("!I", header)[0]
    if not 0 < length <= MAX_FRAME:
        raise S1AProtocolError("invalid Windows pipe frame size")
    return decode_frame(header + _read_exact(k, handle, length, deadline))


def _write(k, handle: int, payload: dict, deadline: float) -> None:
    data = encode_frame(payload)
    buffer = ctypes.create_string_buffer(data)
    def begin(ov: OVERLAPPED) -> bool:
        return bool(k.WriteFile(ctypes.c_void_p(handle), buffer, len(data),
                                None, ctypes.byref(ov)))
    if _overlapped(k, handle, begin, deadline) != len(data):
        raise S1AProtocolError("incomplete Windows pipe write")


def _acknowledge_complete_response(k, handle: int, deadline: float) -> None:
    """One-byte transport ACK only AFTER decode_frame has validated DENY."""
    ack = ctypes.create_string_buffer(TRANSPORT_FRAME_CONSUMED_ACK)
    def begin(ov: OVERLAPPED) -> bool:
        return bool(k.WriteFile(ctypes.c_void_p(handle), ack,
                                len(TRANSPORT_FRAME_CONSUMED_ACK), None,
                                ctypes.byref(ov)))
    if _overlapped(k, handle, begin, deadline) != len(TRANSPORT_FRAME_CONSUMED_ACK):
        raise S1AProtocolError("incomplete transport-consumption ACK")


def _await_response_consumption(k, handle: int, deadline: float) -> None:
    """Do not disconnect while unread response payload remains in pipe.

    Fail closed on missing/malformed ACK; deadline/cancellation is bounded by
    REQUEST_TIMEOUT_S. No FlushFileBuffers, no blocking wait for hostile peers.
    """
    ack = _read_exact(k, handle, len(TRANSPORT_FRAME_CONSUMED_ACK), deadline)
    if ack != TRANSPORT_FRAME_CONSUMED_ACK:
        raise S1AProtocolError("invalid response-consumption ACK")


def _token_sid(k, a, token: int, info_class: int) -> str:
    needed = w.DWORD()
    a.GetTokenInformation(ctypes.c_void_p(token), info_class, None, 0,
                          ctypes.byref(needed))
    if not ctypes.sizeof(ctypes.c_void_p) <= needed.value <= 65536:
        raise S1AProtocolError("invalid token info length")
    data = ctypes.create_string_buffer(needed.value)
    if not a.GetTokenInformation(ctypes.c_void_p(token), info_class, data,
                                  needed, ctypes.byref(needed)):
        raise S1AProtocolError("cannot read impersonated token")
    sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
    string = ctypes.c_void_p()
    if not sid or not a.ConvertSidToStringSidW(sid, ctypes.byref(string)):
        raise S1AProtocolError("kernel token has invalid SID")
    try:
        return ctypes.wstring_at(string)
    finally:
        k.LocalFree(string)


def _peer_authorized(k, a, handle: int, expected_client_sid: str) -> bool:
    if not a.ImpersonateNamedPipeClient(ctypes.c_void_p(handle)):
        return False
    token = ctypes.c_void_p()
    try:
        if not a.OpenThreadToken(k.GetCurrentThread(), TOKEN_QUERY, True,
                                 ctypes.byref(token)):
            return False
        actual = _token_sid(k, a, token.value, 1)  # TokenUser
        integrity = _token_sid(k, a, token.value, 25)  # TokenIntegrityLevel
        try:
            mandatory_rid = int(integrity.rsplit("-", 1)[-1])
        except ValueError:
            return False
        return (actual.casefold() == expected_client_sid.casefold()
                and 8192 <= mandatory_rid < 12288)  # real medium integrity only
    finally:
        if token.value:
            k.CloseHandle(token)
        if not a.RevertToSelf():
            # Impersonation state is now UNKNOWN; never serve another request.
            os._exit(87)


def client_request(pipe_name: str, request: dict) -> dict:
    k, _ = _winlibs()
    _validate_pipe(pipe_name)
    if not k.WaitNamedPipeW(w.LPCWSTR(pipe_name), 2000):
        raise S1AProtocolError("Windows broker service unavailable")
    handle = k.CreateFileW(w.LPCWSTR(pipe_name), CLIENT_OPEN_RIGHTS,
                            0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
    if handle in (None, INVALID):
        raise S1AProtocolError("private Windows pipe open failed")
    try:
        deadline = time.monotonic() + CLIENT_TIMEOUT_S
        _write(k, handle, request, deadline)
        response = _receive(k, handle, deadline)
        if response.get("status") != "DENY":
            raise S1AProtocolError("S1-A accepts DENY-only broker frames")
        # The server keeps its pipe instance alive until this ACK arrives.
        _acknowledge_complete_response(k, handle, deadline)
        return response
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


class WindowsAuthenticatedPipe:
    def __init__(self, name: str, *, service_sid: str,
                 authorized_client_sid: str, protocol: DenyOnlyProtocol) -> None:
        _winlibs()
        _validate_pipe(name)
        if not SID_RE.fullmatch(service_sid) or not service_sid.startswith("S-1-5-80-"):
            raise S1AProtocolError("real Windows SCM service SID required")
        if (not SID_RE.fullmatch(authorized_client_sid)
                or not authorized_client_sid.startswith("S-1-5-21-")
                or service_sid == authorized_client_sid):
            raise S1AProtocolError("distinct unprivileged client SID required")
        if CLIENT_PIPE_RIGHTS & FILE_CREATE_PIPE_INSTANCE:
            raise S1AProtocolError("untrusted client could create pipe instance")
        self.name = name
        self.service_sid = service_sid
        self.client_sid = authorized_client_sid
        self.protocol = protocol

    def serve(self, stop: threading.Event) -> None:
        k, a = _winlibs()
        sd = ctypes.c_void_p()
        sddl = (f"D:P(A;;GA;;;SY)(A;;GA;;;{self.service_sid})"
                f"(A;;0x{CLIENT_PIPE_RIGHTS:08x};;;{self.client_sid})")
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(sd), None):
            raise S1AProtocolError("cannot install explicit named pipe DACL")
        attrs = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
        try:
            while not stop.is_set():
                handle = k.CreateNamedPipeW(
                    w.LPCWSTR(self.name), PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1, 8192, 8192, 2000, ctypes.byref(attrs))
                if handle in (None, INVALID):
                    raise S1AProtocolError("native broker pipe create failed")
                try:
                    try:
                        _overlapped(
                            k, handle,
                            lambda ov: bool(k.ConnectNamedPipe(
                                ctypes.c_void_p(handle), ctypes.byref(ov))),
                            time.monotonic() + CONNECT_TIMEOUT_S,
                            already_connected_ok=True, stop=stop)
                    except S1AProtocolError:
                        # Connect wait is bounded so SCM STOP is responsive;
                        # a stalled peer never pins the service indefinitely.
                        continue
                    if stop.is_set():
                        break
                    deadline = time.monotonic() + REQUEST_TIMEOUT_S
                    try:
                        request = _receive(k, handle, deadline)
                        peer = _peer_authorized(k, a, handle, self.client_sid)
                        response = self.protocol.respond(request, peer_authenticated=peer)
                        if not peer:
                            response["reason"] = "FOREIGN_WINDOWS_TOKEN"
                        _write(k, handle, response, deadline)
                        # Overlapped write only queued bytes. A client may have
                        # read the header but not the body; immediately calling
                        # DisconnectNamedPipe truncates the frame (Win32 233).
                        # An ACK after full client decode is the bounded fence.
                        _await_response_consumption(k, handle, deadline)
                    except (S1AProtocolError, OSError, ValueError):
                        continue
                finally:
                    k.DisconnectNamedPipe(ctypes.c_void_p(handle))
                    k.CloseHandle(ctypes.c_void_p(handle))
        finally:
            k.LocalFree(sd)
