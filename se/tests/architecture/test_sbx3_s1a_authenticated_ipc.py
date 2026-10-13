"""Strict S1-A security selectors for owner #164 and CI #417.

S1-A_OS_REQUIRED tests use REAL separate native OS identities and installed
service. Missing configuration is assertion FAIL, never pytest.skip/xfail.
Portable tests are diagnostic only and cannot certify OS authentication.
"""
from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from se.src.runtimes.capability.native_privileged_broker_s1a import (
    DenyOnlyProtocol, S1AProtocolError, canonical_generation, decode_frame,
    encode_frame, new_request,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    assert value, f"MANDATORY native S1-A environment {name} missing: HARD RED"
    return value


def test_s1a_portable_generation_aliases_fail_closed() -> None:
    assert canonical_generation(1) == "1"
    assert canonical_generation("1") == "1"
    for invalid in (0, True, "0", "01", "+1", "1.0", " 1", "1 ", "-1", "１２", "1e0",
                    9223372036854775808, "9223372036854775808"):
        with pytest.raises(S1AProtocolError):
            canonical_generation(invalid)


def test_s1a_portable_bounded_frame_and_zero_effects() -> None:
    protocol = DenyOnlyProtocol("a" * 32)
    for action in ("MINT", "SPAWN", "GET_POSITIVE_RECEIPT", "DELETE_ROOT", "VERIFIED_EMPTY"):
        request = new_request(action, broker_epoch=protocol.epoch)
        assert protocol.respond(request, peer_authenticated=True)["status"] == "DENY"
    good = new_request("PING", broker_epoch=protocol.epoch)
    assert decode_frame(encode_frame(good)) == good
    assert protocol.respond(good, peer_authenticated=True)["peer_authenticated"] is True
    assert protocol.respond(good, peer_authenticated=True)["peer_authenticated"] is False
    with pytest.raises(S1AProtocolError):
        decode_frame(b"\0\0\x10\x01{}")
    with pytest.raises(S1AProtocolError):
        decode_frame(b'\0\0\0\r{"x":1,"x":2}')


def test_s1a_portable_protected_registration_remains_non_authorizing(tmp_path: Path) -> None:
    from se.src.runtimes.capability.native_broker_registration_s1a import (
        ProtectedRegistrationLedger, S1ARegistrationUnavailable,
    )
    state = tmp_path / "broker-only"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    ledger = ProtectedRegistrationLedger(state)
    first_epoch = ledger.epoch
    ledger.revoke(owner_id="owner", execution_id="exec", sandbox_id="sandbox", generation="1")
    ledger.revoke(owner_id="owner", execution_id="exec", sandbox_id="sandbox", generation=1)
    with pytest.raises(S1AProtocolError):
        ledger.revoke(owner_id="owner", execution_id="exec", sandbox_id="sandbox", generation="01")
    assert ledger.authorizes(owner_id="owner", execution_id="exec", generation="2") is False
    for method in ("mint", "positive_receipt", "verified_empty"):
        with pytest.raises(S1ARegistrationUnavailable):
            getattr(ledger, method)()
    restarted = ProtectedRegistrationLedger(state)
    assert restarted.epoch != first_epoch
    assert restarted.authorizes() is False
    with restarted._db() as db:
        assert db.execute("SELECT COUNT(*) FROM s1a_revocations").fetchone()[0] == 1


def test_s1a_portable_replay_memory_budget_fails_closed() -> None:
    p = DenyOnlyProtocol("a" * 32)
    for n in range(4096):
        request = new_request("PING", broker_epoch=p.epoch, nonce=f"{n:032x}")
        response = p.respond(request, peer_authenticated=True)
        assert response["status"] == "DENY"
        assert response["peer_authenticated"] is True
    assert len(p._seen) == 4096
    overflow = p.respond(new_request("SPAWN", broker_epoch=p.epoch,
                                      nonce="f" * 32), peer_authenticated=True)
    assert overflow["status"] == "DENY"
    assert overflow["peer_authenticated"] is False
    assert "replay table" in overflow["reason"]
    assert len(p._seen) == 4096


def _probe_linux_as(user: str, *, sock: str, epoch: str, action: str = "PING",
                    generation: str = "1") -> subprocess.CompletedProcess[str]:
    command = ["sudo", "-n", "-u", user, sys.executable, "-m",
               "se.src.runtimes.capability.native_privileged_broker_s1a",
               "--linux-socket", sock, "--epoch", epoch,
               "--action", action, "--generation", generation]
    return subprocess.run(command, cwd=str(Path(__file__).resolve().parents[3]),
                          capture_output=True, text=True, timeout=15, check=False)


def _linux_setup() -> tuple[str, str, str, int]:
    assert sys.platform.startswith("linux"), "S1-A Linux native evidence requires real Linux"
    sock = _required("SBX3_S1A_TEST_SOCKET")
    broker_uid = int(_required("SBX3_S1A_TEST_BROKER_UID"))
    allowed_user = _required("SBX3_S1A_TEST_CLIENT_USER")
    foreign_user = _required("SBX3_S1A_TEST_FOREIGN_USER")
    st = os.lstat(sock)
    assert stat.S_ISSOCK(st.st_mode) and st.st_uid == broker_uid
    assert stat.S_IMODE(st.st_mode) == 0o660
    assert broker_uid != os.geteuid(), "CI owner is not privileged broker identity"
    for user in (allowed_user, foreign_user):
        result = subprocess.run(["id", "-u", user], capture_output=True, text=True, check=True)
        assert int(result.stdout.strip()) not in (broker_uid, os.geteuid())
    assert allowed_user != foreign_user
    return sock, allowed_user, foreign_user, broker_uid


# Ordinary Architecture Baseline runs all tests without privileged service setup.
# These native-positive selectors exist ONLY when #417 sets its explicit strict
# admission flag. #417 must hard-fail collection-count/ID mismatches; NEVER skip.
if os.environ.get("SBX3_S1A_NATIVE_REQUIRED") == "1":
    def test_s1a_linux_native_distinct_uid_so_peercred_transport_denies_effects() -> None:
        sock, client, foreign, broker_uid = _linux_setup()
        # The first DENY response offers a non-secret process epoch. This cannot
        # authorize an execution; use it only to pin this service instance.
        first = _probe_linux_as(client, sock=sock, epoch="")
        assert first.returncode == 0, first.stderr
        unknown_epoch = json.loads(first.stdout)
        assert unknown_epoch["status"] == "DENY"
        epoch = unknown_epoch["broker_epoch"]
        assert re.fullmatch("[a-f0-9]{32}", epoch)

        authenticated = _probe_linux_as(client, sock=sock, epoch=epoch)
        assert authenticated.returncode == 0, authenticated.stderr
        result = json.loads(authenticated.stdout)
        assert result["status"] == "DENY" and result["peer_authenticated"] is True
        assert result["reason"] == "TRANSPORT_ONLY_NO_EFFECT_AUTHORITY"
        for action in ("MINT", "SPAWN", "GET_POSITIVE_RECEIPT", "DELETE_ROOT", "VERIFIED_EMPTY"):
            proc = _probe_linux_as(client, sock=sock, epoch=epoch, action=action)
            assert proc.returncode == 0, proc.stderr
            r = json.loads(proc.stdout)
            assert r["status"] == "DENY" and r["reason"] == "S1A_NATIVE_EFFECT_NOT_RELEASED"
        attacker = _probe_linux_as(foreign, sock=sock, epoch=epoch)
        # DAC can deny connect outright, or peer-authenticated service may deny it.
        if attacker.returncode == 0:
            bad = json.loads(attacker.stdout)
            assert bad["status"] == "DENY" and bad["peer_authenticated"] is False
        else:
            assert attacker.stderr


    def test_s1a_linux_native_alias_replay_stale_and_missing_socket_deny() -> None:
        sock, client, _, _ = _linux_setup()
        first = _probe_linux_as(client, sock=sock, epoch="")
        epoch = json.loads(first.stdout)["broker_epoch"]
        bad = _probe_linux_as(client, sock=sock, epoch=epoch, generation="01")
        assert bad.returncode != 0  # local canonical check rejects before IPC
        stale = _probe_linux_as(client, sock=sock, epoch="f" * 32)
        assert stale.returncode == 0
        assert json.loads(stale.stdout)["peer_authenticated"] is False
        nonexistent = _probe_linux_as(client, sock=sock + ".missing", epoch=epoch)
        assert nonexistent.returncode != 0  # No host/HTTP fallback


    def test_s1a_linux_native_hard_crash_restart_rejects_old_epoch() -> None:
        """#417 must hard-kill broker PID then launch real broker UID again."""
        sock, client, _, broker_uid = _linux_setup()
        old_epoch = _required("SBX3_S1A_TEST_PREVIOUS_EPOCH")
        old_pid = int(_required("SBX3_S1A_TEST_PREVIOUS_SERVICE_PID"))
        current_pid = int(_required("SBX3_S1A_TEST_CURRENT_SERVICE_PID"))
        assert old_pid > 0 and current_pid > 0 and old_pid != current_pid
        status = Path(f"/proc/{current_pid}/status").read_text()
        uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
        assert int(uid_line.split()[1]) == broker_uid
        cmd = Path(f"/proc/{current_pid}/cmdline").read_bytes()
        assert b"native_broker_service_s1a" in cmd
        old = _probe_linux_as(client, sock=sock, epoch=old_epoch)
        assert old.returncode == 0, old.stderr
        denied = json.loads(old.stdout)
        assert denied["status"] == "DENY" and denied["peer_authenticated"] is False
        fresh_epoch = denied["broker_epoch"]
        assert fresh_epoch != old_epoch
        new = _probe_linux_as(client, sock=sock, epoch=fresh_epoch)
        assert new.returncode == 0, new.stderr
        transport = json.loads(new.stdout)
        assert transport["status"] == "DENY"
        assert transport["peer_authenticated"] is True
        assert json.loads(_probe_linux_as(
            client, sock=sock, epoch=fresh_epoch, action="SPAWN").stdout)["status"] == "DENY"


    def _windows_setup() -> tuple[str, str]:
        assert sys.platform == "win32", "S1-A native Windows evidence requires Windows"
        pipe = _required("SBX3_S1A_TEST_PIPE")
        service = _required("SBX3_S1A_TEST_SERVICE")
        service_sid = _required("SBX3_S1A_TEST_SERVICE_SID")
        client_sid = _required("SBX3_S1A_TEST_CLIENT_SID")
        assert pipe.startswith("\\\\.\\pipe\\SBX3S1A-")
        sc = subprocess.run(["sc.exe", "queryex", service], capture_output=True,
                            text=True, check=True)
        assert "RUNNING" in sc.stdout and re.search(r"PID\s*:\s*[1-9][0-9]*", sc.stdout)
        sid_info = subprocess.run(["sc.exe", "showsid", service], capture_output=True,
                                  text=True, check=True)
        assert service_sid in sid_info.stdout
        whoami = subprocess.run(["whoami", "/user"], capture_output=True, text=True, check=True)
        assert client_sid.casefold() in whoami.stdout.casefold()
        assert client_sid.casefold() != service_sid.casefold()
        assert "S-1-5-18" not in whoami.stdout  # no LocalSystem test double
        groups = subprocess.run(["whoami", "/groups"], capture_output=True, text=True, check=True)
        assert "S-1-16-12288" not in groups.stdout and "S-1-16-16384" not in groups.stdout, (
            "native Windows client must not be high-integrity/admin CI operator"
        )
        return pipe, service


    def test_s1a_windows_native_scm_sid_impersonated_token_transport_denies_effects() -> None:
        pipe, _ = _windows_setup()
        from se.src.runtimes.capability.native_broker_ipc_windows_s1a import client_request
        epoch_response = client_request(pipe, new_request(broker_epoch=""))
        assert epoch_response["status"] == "DENY"
        epoch = epoch_response["broker_epoch"]
        result = client_request(pipe, new_request(broker_epoch=epoch))
        assert result["status"] == "DENY"
        assert result["peer_authenticated"] is True  # from impersonated kernel token
        for action in ("MINT", "SPAWN", "GET_POSITIVE_RECEIPT", "DELETE_ROOT", "VERIFIED_EMPTY"):
            r = client_request(pipe, new_request(action, broker_epoch=epoch))
            assert r["status"] == "DENY" and r["reason"] == "S1A_NATIVE_EFFECT_NOT_RELEASED"


    def test_s1a_windows_native_stale_epoch_and_no_service_fallback() -> None:
        pipe, _ = _windows_setup()
        from se.src.runtimes.capability.native_broker_ipc_windows_s1a import client_request
        response = client_request(pipe, new_request(broker_epoch="f" * 32))
        assert response["status"] == "DENY"
        assert response["peer_authenticated"] is False
        with pytest.raises(S1AProtocolError):
            client_request(pipe + "-missing", new_request(broker_epoch=""))


    def test_s1a_windows_native_stalled_peer_read_deadline_recovers() -> None:
        """No bytes from a real allowed client must not hang SCM listener."""
        pipe, _ = _windows_setup()
        from se.src.runtimes.capability.native_broker_ipc_windows_s1a import (
            _winlibs, client_request, GENERIC_READ, GENERIC_WRITE,
            FILE_FLAG_OVERLAPPED, OPEN_EXISTING, INVALID, REQUEST_TIMEOUT_S,
        )
        import ctypes
        from ctypes import wintypes as w
        k, _ = _winlibs()
        assert k.WaitNamedPipeW(w.LPCWSTR(pipe), 2000)
        h = k.CreateFileW(w.LPCWSTR(pipe), GENERIC_READ | GENERIC_WRITE,
                          0, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        assert h not in (None, INVALID)
        try:
            # A client opens the pipe and deliberately never sends its frame.
            # A bounded ReadFile must cancel, drain, and release the instance.
            time.sleep(REQUEST_TIMEOUT_S + 0.8)
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
        beginning = time.monotonic()
        fresh = client_request(pipe, new_request(broker_epoch=""))
        assert time.monotonic() - beginning < 6
        assert fresh["status"] == "DENY"
        assert re.fullmatch("[a-f0-9]{32}", fresh["broker_epoch"])


    def test_s1a_windows_native_hard_crash_restart_rejects_old_epoch() -> None:
        """#417 operator hard-kills SCM PID; fresh SCM service must rotate epoch."""
        pipe, service = _windows_setup()
        prior_epoch = _required("SBX3_S1A_TEST_PREVIOUS_EPOCH")
        prior_pid = int(_required("SBX3_S1A_TEST_PREVIOUS_SERVICE_PID"))
        sc = subprocess.run(["sc.exe", "queryex", service], capture_output=True,
                            text=True, check=True)
        match = re.search(r"PID\s*:\s*([1-9][0-9]*)", sc.stdout)
        assert match
        current_pid = int(match.group(1))
        assert current_pid != prior_pid
        from se.src.runtimes.capability.native_broker_ipc_windows_s1a import client_request
        old = client_request(pipe, new_request(broker_epoch=prior_epoch))
        assert old["status"] == "DENY" and old["peer_authenticated"] is False
        epoch = old["broker_epoch"]
        assert epoch != prior_epoch
        authentic = client_request(pipe, new_request(broker_epoch=epoch))
        assert authentic["status"] == "DENY" and authentic["peer_authenticated"] is True
        for action in ("SPAWN", "GET_POSITIVE_RECEIPT", "VERIFIED_EMPTY", "DELETE_ROOT"):
            denied = client_request(pipe, new_request(action, broker_epoch=epoch))
            assert denied["status"] == "DENY"
            assert denied["reason"] == "S1A_NATIVE_EFFECT_NOT_RELEASED"
