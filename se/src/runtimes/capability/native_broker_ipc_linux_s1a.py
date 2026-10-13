"""Actual Linux AF_UNIX + kernel SO_PEERCRED transport; never native effects."""
from __future__ import annotations

import os
import socket
import stat
import struct
import time
import threading
from pathlib import Path
from .native_privileged_broker_s1a import (
    MAX_FRAME, DenyOnlyProtocol, S1AProtocolError, decode_frame, encode_frame,
)


SERVER_REQUEST_TIMEOUT_S = 3.0
CLIENT_REQUEST_TIMEOUT_S = 5.0


def _set_remaining(sock: socket.socket, deadline: float) -> None:
    # A timeout on each recv alone lets a drip-feed client pin this
    # single-threaded broker forever. All reads share one monotonic deadline.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise S1AProtocolError("bounded Linux IPC deadline exceeded")
    sock.settimeout(remaining)


def _read_exact(sock: socket.socket, count: int, *, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < count:
        _set_remaining(sock, deadline)
        try:
            part = sock.recv(count - len(result))
        except socket.timeout as exc:
            raise S1AProtocolError("bounded Linux IPC deadline exceeded") from exc
        if not part:
            raise S1AProtocolError("truncated IPC frame")
        result.extend(part)
    return bytes(result)


def _receive(sock: socket.socket, *, deadline: float) -> dict:
    header = _read_exact(sock, 4, deadline=deadline)
    n = struct.unpack("!I", header)[0]
    if n == 0 or n > MAX_FRAME:
        raise S1AProtocolError("invalid frame size")
    return decode_frame(header + _read_exact(sock, n, deadline=deadline))


def client_request(sock_path: str, request: dict) -> dict:
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        raise S1AProtocolError("native Linux SO_PEERCRED not available")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        deadline = time.monotonic() + CLIENT_REQUEST_TIMEOUT_S
        _set_remaining(conn, deadline)
        conn.connect(sock_path)
        _set_remaining(conn, deadline)
        conn.sendall(encode_frame(request))
        return _receive(conn, deadline=deadline)


class LinuxAuthenticatedSocket:
    """Broker must run in a distinct UID from clients.

    Parent directory is CI/operator-provisioned: broker-owned, mode 0710,
    expected group. The ledger lives in a DIFFERENT broker-only 0700 directory.
    """

    def __init__(self, path: str, *, allowed_uid: int, protocol: DenyOnlyProtocol) -> None:
        if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
            raise S1AProtocolError("Linux kernel peer credentials required")
        if type(allowed_uid) is not int or allowed_uid < 0 or allowed_uid == os.geteuid():
            raise S1AProtocolError("client must have a distinct allowed OS UID")
        self.path = Path(path).absolute()
        parent = self.path.parent
        st = parent.stat(follow_symlinks=False)
        if (parent.is_symlink() or not stat.S_ISDIR(st.st_mode)
                or st.st_uid != os.geteuid() or st.st_mode & 0o007
                or st.st_mode & 0o020 or st.st_mode & 0o200 == 0):
            raise S1AProtocolError("unsafe broker socket parent")
        if self.path.exists() or self.path.is_symlink():
            # Never unlink a stale or attacker-replaced socket automatically.
            raise S1AProtocolError("socket path already occupied")
        self.allowed_uid = allowed_uid
        self.protocol = protocol
        self._inode: tuple[int, int] | None = None

    def _path_ok(self) -> bool:
        try:
            st = self.path.lstat()
            return stat.S_ISSOCK(st.st_mode) and (st.st_dev, st.st_ino) == self._inode and st.st_uid == os.geteuid()
        except OSError:
            return False

    def serve(self, stop: threading.Event) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o660)
            st = self.path.lstat()
            self._inode = (st.st_dev, st.st_ino)
            listener.listen(16)
            listener.settimeout(0.25)
            try:
                while not stop.is_set():
                    try:
                        conn, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with conn:
                        deadline = time.monotonic() + SERVER_REQUEST_TIMEOUT_S
                        if not self._path_ok():
                            stop.set()
                            continue
                        try:
                            cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                            pid, uid, gid = struct.unpack("3i", cred)
                            # UID from kernel, never an untrusted message field.
                            trusted = pid > 0 and uid == self.allowed_uid and uid != os.geteuid()
                            req = _receive(conn, deadline=deadline)
                            response = self.protocol.respond(req, peer_authenticated=trusted)
                            if not trusted:
                                response["reason"] = "FOREIGN_KERNEL_PEER"
                            _set_remaining(conn, deadline)
                            conn.sendall(encode_frame(response))
                        except (OSError, ValueError, S1AProtocolError, TimeoutError):
                            # Do not leak privileged state; no spawn or fallback.
                            continue
            finally:
                if self._path_ok():
                    self.path.unlink()
