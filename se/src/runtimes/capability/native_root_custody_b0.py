"""SBX-3 B0 cross-process NEGATIVE quarantine/cleanup serialization.

All cooperative SandboxManager instances sharing a base_root use the same
OS-held advisory lock while creating a native tombstone or removing a root.
The lock is NOT a production cleanup permit, nor does it authorize a process
to launch outside the OS-owned native broker. Host/same-UID mutation and
noncooperating native processes remain a production blocker.
"""

from __future__ import annotations

import errno
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class NativeCustodyUnavailable(RuntimeError):
    """Cross-process custody cannot be trusted; fail closed."""


class NativeRootCustodyGate:
    """Bind one stable OS file-lock inode across a SandboxManager lifetime.

    The stable inode check matters: locking a freshly substituted pathname
    would synchronize with the wrong inode and allow a conflicting writer.
    The lock is in the manager control directory, NEVER a sandbox-child path.
    """

    _NAME = ".root-lifecycle-lock"

    def __init__(self, control_dir: Path) -> None:
        self.path = control_dir / self._NAME
        self._control_dir = control_dir
        self._dir_id = self._identity(control_dir, directory=True)
        fd = self._open()
        try:
            self._file_id = self._identity_fd(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _identity(path: Path, *, directory: bool) -> tuple[int, int]:
        try:
            record = path.lstat()
        except OSError as exc:
            raise NativeCustodyUnavailable("native custody control missing") from exc
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(record.st_mode):
            raise NativeCustodyUnavailable("native custody object redirected or invalid")
        if os.name == "posix" and record.st_mode & (stat.S_IWOTH | stat.S_IWGRP):
            raise NativeCustodyUnavailable("native custody permissions allow third parties")
        if record.st_nlink < 1:
            raise NativeCustodyUnavailable("native custody inode no longer linked")
        return record.st_dev, record.st_ino

    @staticmethod
    def _identity_fd(fd: int) -> tuple[int, int]:
        record = os.fstat(fd)
        if not stat.S_ISREG(record.st_mode) or record.st_nlink < 1:
            raise NativeCustodyUnavailable("native custody lock is not a regular file")
        return record.st_dev, record.st_ino

    def _open(self) -> int:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise NativeCustodyUnavailable("native custody lock inaccessible") from exc
        try:
            # Windows msvcrt locking needs one addressable byte. O_CREAT
            # does not truncate an existing lock file.
            if os.name == "nt" and os.fstat(fd).st_size == 0:
                os.write(fd, b"\x00")
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def hold(self, *, timeout_seconds: float = 10.0) -> Iterator[None]:
        """OS process-shared mutex around tombstone CREATE vs root DELETE.

        OS drops a held file lock on hard process termination. The durable
        marker remains, so a restarted manager cannot reinterpret crash as
        VERIFIED_EMPTY. Thread serialization belongs to SandboxManager.RLock.
        """
        if not 0 < timeout_seconds <= 30:
            raise ValueError("invalid native custody timeout")
        if self._identity(self._control_dir, directory=True) != self._dir_id:
            raise NativeCustodyUnavailable("native custody directory was replaced")
        fd = self._open()
        locked = False
        try:
            if self._identity_fd(fd) != self._file_id:
                raise NativeCustodyUnavailable("native custody lock inode was replaced")
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if os.name != "nt" and exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise NativeCustodyUnavailable("native custody OS lock failed") from exc
                    if time.monotonic() >= deadline:
                        raise NativeCustodyUnavailable("native custody OS lock timed out") from exc
                    time.sleep(0.02)

            if (
                self._identity(self._control_dir, directory=True) != self._dir_id
                or self._identity(self.path, directory=False) != self._file_id
                or self._identity_fd(fd) != self._file_id
            ):
                raise NativeCustodyUnavailable("native custody lock path changed while acquiring")
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


__all__ = ["NativeCustodyUnavailable", "NativeRootCustodyGate"]
