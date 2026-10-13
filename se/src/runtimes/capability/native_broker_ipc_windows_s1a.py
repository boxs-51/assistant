"""Native Windows local named pipe: explicit DACL, SCM SID, impersonated TokenUser.

No mock Windows identity, no PID/JSON trust, no production spawn or receipt.
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import threading
from ctypes import wintypes as w
from .native_privileged_broker_s1a import MAX_FRAME, DenyOnlyProtocol, S1AProtocolError, decode_frame, encode_frame

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
TOKEN_QUERY = 0x0008
ERROR_PIPE_CONNECTED = 535
INVALID = ctypes.c_void_p(-1).value
SID_RE = re.compile(r"S-1-(?:[0-9]+-)*[0-9]+\Z")


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", w.BOOL)]


def _winlibs():
    if os.name != "nt":
        raise S1AProtocolError("native Windows only, no emulated pipe")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    for fn in ("CreateNamedPipeW", "CreateFileW"):
        getattr(k, fn).restype = ctypes.c_void_p
    for fn in ("GetCurrentThread",):
        getattr(k, fn).restype = ctypes.c_void_p
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    a.ImpersonateNamedPipeClient.argtypes = [ctypes.c_void_p]
    a.ImpersonateNamedPipeClient.restype = w.BOOL
    a.OpenThreadToken.argtypes = [ctypes.c_void_p, w.DWORD, w.BOOL, ctypes.POINTER(ctypes.c_void_p)]
    a.OpenThreadToken.restype = w.BOOL
    a.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    a.GetTokenInformation.restype = w.BOOL
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertSidToStringSidW.restype = w.BOOL
    return k, a


def _valid_sid(value: str) -> str:
    if not isinstance(value, str) or not SID_RE.fullmatch(value):
        raise S1AProtocolError("invalid expected Windows SID")
    return value


def _read_exact(k, h, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        size = min(count - len(data), 4096)
        buf = ctypes.create_string_buffer(size)
        done = w.DWORD()
        if not k.ReadFile(ctypes.c_void_p(h), buf, size, ctypes.byref(done), None) or not done.value:
            raise S1AProtocolError("Windows pipe read failed")
        data.extend(buf.raw[:done.value])
    return bytes(data)


def _receive(k, h) -> dict:
    header = _read_exact(k, h, 4)
    n = struct.unpack("!I", header)[0]
    if not 0 < n <= MAX_FRAME:
        raise S1AProtocolError("invalid pipe frame length")
    return decode_frame(header + _read_exact(k, h, n))


def _write(k, h, payload: dict) -> None:
    raw = encode_frame(payload)
    done = w.DWORD()
    buf = ctypes.create_string_buffer(raw)
    if not k.WriteFile(ctypes.c_void_p(h), buf, len(raw), ctypes.byref(done), None) or done.value != len(raw):
        raise S1AProtocolError("Windows pipe write failed")


def _token_sid(a, token, info_class: int) -> str:
    needed = w.DWORD()
    a.GetTokenInformation(ctypes.c_void_p(token), info_class, None, 0, ctypes.byref(needed))
    if needed.value < ctypes.sizeof(ctypes.c_void_p) or needed.value > 65536:
        raise S1AProtocolError("invalid impersonated token info size")
    data = ctypes.create_string_buffer(needed.value)
    if not a.GetTokenInformation(ctypes.c_void_p(token), info_class, data, needed, ctypes.byref(needed)):
        raise S1AProtocolError("unable to inspect impersonated token")
    # TOKEN_USER and TOKEN_MANDATORY_LABEL each begin with SID_AND_ATTRIBUTES.
    sid_addr = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
    out = ctypes.c_void_p()
    if not sid_addr or not a.ConvertSidToStringSidW(sid_addr, ctypes.byref(out)):
        raise S1AProtocolError("unable to stringify kernel token SID")
    try:
        return ctypes.wstring_at(out)
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(out)


def _peer_authorized(k, a, pipe, expected_client_sid: str) -> bool:
    if not a.ImpersonateNamedPipeClient(ctypes.c_void_p(pipe)):
        return False
    token = ctypes.c_void_p()
    try:
        if not a.OpenThreadToken(k.GetCurrentThread(), TOKEN_QUERY, True, ctypes.byref(token)):
            return False
        actual = _token_sid(a, token.value, 1)
        integrity = _token_sid(a, token.value, 25)
        try:
            mandatory_rid = int(integrity.rsplit("-", 1)[-1])
        except ValueError:
            return False
        return actual.casefold() == expected_client_sid.casefold() and mandatory_rid >= 8192
    finally:
        if token.value:
            k.CloseHandle(token)
        # Revert even on malformed token/failed SID lookup.
        if not a.RevertToSelf():
            raise S1AProtocolError("CRITICAL: Windows impersonation revert failed")


def client_request(pipe_name: str, request: dict) -> dict:
    k, _ = _winlibs()
    if not pipe_name.startswith("\\\\.\\pipe\\SBX3S1A-"):
        raise S1AProtocolError("pipe outside SBX3 private namespace")
    # Never use local filesystem/localhost TCP fallback.
    k.WaitNamedPipeW(w.LPCWSTR(pipe_name), w.DWORD(5000))
    h = k.CreateFileW(w.LPCWSTR(pipe_name), GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
    if h in (None, INVALID):
        raise S1AProtocolError("authorized Windows pipe unavailable")
    try:
        _write(k, h, request)
        return _receive(k, h)
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


class WindowsAuthenticatedPipe:
    def __init__(self, name: str, *, service_sid: str,
                 authorized_client_sid: str, protocol: DenyOnlyProtocol) -> None:
        _winlibs()
        if not name.startswith("\\\\.\\pipe\\SBX3S1A-") or "/" in name:
            raise S1AProtocolError("invalid local-only pipe namespace")
        self.name = name
        self.service_sid = _valid_sid(service_sid)
        self.client_sid = _valid_sid(authorized_client_sid)
        if not self.service_sid.startswith("S-1-5-80-"):
            raise S1AProtocolError("broker requires a real SCM service SID")
        if (not self.client_sid.startswith("S-1-5-21-")
                or self.client_sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}
                or self.service_sid == self.client_sid):
            raise S1AProtocolError("client must be a distinct unprivileged user SID")
        self.protocol = protocol

    def serve(self, stop: threading.Event) -> None:
        k, a = _winlibs()
        sd = ctypes.c_void_p()
        # No Everyone, Anonymous or default discretionary access.
        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{self.service_sid})(A;;GRGW;;;{self.client_sid})"
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None):
            raise S1AProtocolError("explicit named pipe DACL unavailable")
        attrs = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
        try:
            while not stop.is_set():
                h = k.CreateNamedPipeW(w.LPCWSTR(self.name), PIPE_ACCESS_DUPLEX,
                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1, 8192, 8192, 2000, ctypes.byref(attrs))
                if h in (None, INVALID):
                    raise S1AProtocolError("native pipe creation failed")
                try:
                    connected = bool(k.ConnectNamedPipe(ctypes.c_void_p(h), None))
                    if not connected and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
                        if stop.is_set():
                            break
                        continue
                    if stop.is_set():
                        break
                    try:
                        request = _receive(k, h)
                        peer = _peer_authorized(k, a, h, self.client_sid)
                        response = self.protocol.respond(request, peer_authenticated=peer)
                        if not peer:
                            response["reason"] = "FOREIGN_WINDOWS_TOKEN"
                        _write(k, h, response)
                    except (S1AProtocolError, OSError, ValueError):
                        continue
                finally:
                    k.DisconnectNamedPipe(ctypes.c_void_p(h))
                    k.CloseHandle(ctypes.c_void_p(h))
        finally:
            k.LocalFree(sd)
