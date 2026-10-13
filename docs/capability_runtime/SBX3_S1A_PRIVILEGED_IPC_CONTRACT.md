# SBX-3 S1-A v2 — authenticated IPC (transport only)

**Issue:** #164. **Candidate parent:** draft PR #490 at `3b845e4684495b8d62de3fe6503b1836b438bc13`. Seven **ADD** files; #417 owns a different, future CI workflow. **Never merge or enable Agent native tooling on the basis of this stage.**

## Security boundary and deliberate non-features

This stage authenticates a separately launched OS service and the **connecting OS principal** at the transport. No complete CRT-owned, CAS-fenced owner/execution/sandbox/generation/invocation/toolcall registration source has been identified (#156 HOLD). Thus `ProtectedRegistrationLedger.authorizes` returns `False`, `mint`, `positive_receipt` and `verified_empty` reject, and every private IPC action returns **DENY**. `PING` is a deny-only transport observation, not execution authority. No model/API route imports or instantiates this service; no Tools/Agent/AE/CRT production call sites are modified. No fallback to host `Popen`, `shell=True`, loopback HTTP or a same-user Python issuer.

**Prohibited until distinct separately reviewed stages:** native terminal.run/launch/python.run, actual OS spawn/kill, positive receipt, `COMMITTED`/`COMPLETED`, `VERIFIED_EMPTY.production_usable`, sandbox-root deletion or network-NONE certification. Windows Job-only PR #486 is a separate sibling and does not establish LPAC/network NONE.

## Exact S1-A wire grammar

All messages are four-byte big-endian unsigned payload length followed by UTF-8 JSON (max 4096 payload bytes), rejecting malformed/duplicate keys, invalid UTF-8, oversized/truncated content. Request keys **exactly**: `version=1`, `action` in `PING,MINT,SPAWN,GET_POSITIVE_RECEIPT,DELETE_ROOT,VERIFIED_EMPTY`, `generation` (canonical decimal positive signed-64-bit integer represented as ASCII string), `nonce` (32 lowercase ASCII hexadecimal chars), `deadline_unix` (strict wall-clock future <=30 seconds), `broker_epoch` (32-char lowercase random process epoch), `revision=0` (no valid CRT production revision yet). An epoch mismatch is a DENY, but the broker reports its nonsecret current epoch for the client to retry PING. A replayed valid nonce in one service process is denied. **Every request remains DENIED even with successful authentication**.

`generation` must equal its canonical decimal representation: `1` accepted; `01`, `+1`, `1.0`, `0`, Unicode digits, negative, whitespace and overflow rejected. Agent `lease_generation` is an integer and B0 `SpawnIdentity.lease_generation` is a string; this S1-A grammar alone does NOT fix S0, and no B0 file is modified.

Response has `version=1, status=DENY, broker_epoch, peer_authenticated` and an abbreviated reason. `peer_authenticated` describes **observed OS transport peer only** and cannot be used to grant a capability. Unsupported OS, untrustworthy peer, missing service, token error, malformed request, broker crash or epoch changes fail closed. No evidence of successful native effect is ever emitted.

## Physical service contracts for #417

### Linux

An operator-provisioned broker OS UID (different from both test client UIDs), non-shared, broker-owned protected state directory **0700**, and broker-owned IPC directory (example mode **0710** group `sbx3_s1a_ipc`) are required. The broker creates its own AF_UNIX socket with mode 0660. Allowed client group membership permits DAC connect, while the broker checks the **kernel SO_PEERCRED UID** against a specific unprivileged authorized UID; a foreign UID with socket DAC membership must connect but receive `FOREIGN_KERNEL_PEER`. A separate nonmember must fail at the DAC boundary. Parent mode 0700 with unrelated client would incorrectly block intended IPC, and group-writable world directories are disallowed. State must never be under an untrusted sandbox root. Verify path inode/owner/mode; neither client may write state or socket parent.

**Bounded Linux stream lifetime:** a single-threaded broker must not rely on a per-`recv` 3-second timeout, since an allowed client sending one byte every <3 seconds can otherwise monopolize the listener indefinitely. Header+body reception and DENY response share one 3-second `time.monotonic()` deadline from accept; client connect+send+receive share one 5-second deadline. Expiry closes that connection without effect or fallback. Portable incomplete frame and real UID incremental drip-feed/recovery regressions are required inside the existing Linux native selector.

Service invocation, after provisioning a broker-owned JSON config and environment access to the repository Python package:

```bash
sudo -u sbx3_s1a_broker -- /usr/bin/python3 -m se.src.runtimes.capability.native_broker_service_s1a --config /<broker-readable-config>.json
```

Example JSON keys (both OS properties are present even when unused):
```json
{
  "service_name": "SBX3S1ACI",
  "state_directory": "/<broker-owned-0700-state-directory>",
  "linux_socket": "/run/sbx3-s1a/broker.sock",
  "allowed_linux_uid": 12345,
  "windows_pipe": "\\\\.\\pipe\\SBX3S1A-CI",
  "windows_service_sid": "S-1-5-80-<actual-SCM-service-SID>",
  "windows_allowed_client_sid": "S-1-5-21-<actual-unprivileged-test-SID>"
}
```

These are **placeholders**: the CI workflow must replace them with measured runner principal IDs; never use the literal illustrative values. Account and socket provision must be owned by trusted CI/operator, never client JSON. Linux service readiness emits `SBX3_S1A_TRANSPORT_READY epoch=...` (nonsecret), but the runner must verify the OS PID and UID independently.

### Windows (genuine SCM, not a console child)

The same Python module supports **real Win32** `StartServiceCtrlDispatcherW`, `ServiceMain`, `RegisterServiceCtrlHandlerExW`, `SetServiceStatus` and stop handling behind `--scm`. A call outside SCM fails; Python `Start-Process` alone is insufficient. #417 must install the exact tracked image command as `sc.exe create ... type= own obj= "NT AUTHORITY\\LocalService"`, set `sc.exe sidtype ... unrestricted`, start, inspect `RUNNING`/PID and actual service SID/account token independently; ensure the selected Python runtime and broker-owned config/state can be read under LocalService. If Windows SCM cannot execute this Python entrypoint with correct identity and lifecycle, **HARD RED and propose a separately owned extra physical source/build artifact**; do not fake service success.

The tracked native transport calls `CreateNamedPipeW` with `PIPE_REJECT_REMOTE_CLIENTS`, explicit non-Everyone/anonymous SDDL DACL (`SY`, actual service SID, specifically allowed client SID **with hexadecimal `0x00120183` read/write rights, deliberately excluding `FILE_CREATE_PIPE_INSTANCE=0x4`**), calls `ImpersonateNamedPipeClient`, `OpenThreadToken`, `GetTokenInformation(TokenUser/TokenIntegrityLevel)`, checks **actual allowed SID + medium-or-higher integrity**, and always `RevertToSelf`/close handles. #417 must run an **unprivileged client SID genuinely different from the service SID**, plus foreign/remote/low-integrity denials; a single administrator client is insufficient.

No Windows filesystem ACL is asserted solely by Python `os.stat`; #417 must seed a pre-existing explicit Everyone/full-control ACE as an adversarial fixture, replace the complete state ACL with a protected LocalService/SYSTEM/Administrators allowlist, read back the effective DACL and prove from a separate unprivileged client SID that state enumeration/file creation and SQLite/config reads/writes are denied. `icacls /inheritance:r /grant:r` alone does not remove unrelated explicit ACEs. Windows `ProtectedRegistrationLedger` remains an intentionally non-authorizing, DENY-only stage; the OS-side ACL witness must pass before even transport certification.

## Strict executable test selectors (no skip/xfail)

File `se/tests/architecture/test_sbx3_s1a_authenticated_ipc.py` contains:

- Portable, **NOT OS-positive:** six named selectors (generation aliases, frame/effect DENY, durable revoke, Windows pipe create-instance ACL bit, replay memory budget, and Linux whole-request deadline under an incomplete socketpair frame).
- Linux **MANDATORY real separate UID — exactly three native selectors:** `test_s1a_linux_native_distinct_uid_so_peercred_transport_denies_effects`, `test_s1a_linux_native_alias_replay_stale_and_missing_socket_deny`, `test_s1a_linux_native_hard_crash_restart_rejects_old_epoch`. The first includes real slow-client recovery, a foreign UID that passed DAC then failed `SO_PEERCRED`, a separate DAC-denied UID and two on-wire uses of the same nonce.
- Windows **MANDATORY native SCM+token — exactly four selectors:** `test_s1a_windows_native_scm_sid_impersonated_token_transport_denies_effects`, `test_s1a_windows_native_stale_epoch_and_no_service_fallback`, `test_s1a_windows_native_stalled_peer_read_deadline_recovers`, `test_s1a_windows_native_hard_crash_restart_rejects_old_epoch`.

#417 must set `SBX3_S1A_NATIVE_REQUIRED=1` at **collection and execution**; Architecture alone collects portable tests and cannot certify OS principals. Strict native execution requires exactly **Linux 3 and Windows 4** expected selector IDs, zero mandatory skip/xfail, real separate broker/client identities, same-head source SHA and all seven blob pins, hard-kill/restart epochs, raw CI/JUnit evidence, and no missing service/runner identity. One workflow ADD is owned by #417, not #164; failure or absence is RED, never fallback/positive authority.

### Native Windows response-consumption barrier — Win32 233 fix

Real #417 Windows native source-pin `8a86216c0f75d2c335f1a932b9fddc875c73f7ca` **failed all four mandatory tests**: client received the 4-byte DENY frame header but `ReadFile` of payload returned Windows `ERROR_PIPE_NOT_CONNECTED=233`. The original service `WriteFile` completion only meant its response was queued; it immediately called `DisconnectNamedPipe`, which may discard data unread by the client. Windows S1-A now requires a **bounded application-level frame-consumption fence**:

1. Broker sends its complete **DENY** frame using bounded overlapped `WriteFile` (unchanged 2-second total server request deadline).
2. Authorized client receives and **fully validates** the framed JSON response, **requires `status=DENY`**, then writes one literal byte `0x06` to the same overlapped pipe as `TRANSPORT_FRAME_CONSUMED_ACK` (within unchanged 4-second total client I/O deadline).
3. The broker does **not** disconnect immediately after `WriteFile`. It calls the bounded overlapped `ReadFile` for exactly **one ACK byte**, compares against `0x06`, then disconnects. No missing/wrong/short ACK confers authority. Deadline or hostile silent client => `CancelIoEx` + mandatory completion drain before freeing buffers and deny-only disconnection; **no unbounded `FlushFileBuffers`**.
4. The ACK only establishes **response-body consumption**, not authorization, positive receipt, process spawn, CRT completion, AE commit, root release, or `VERIFIED_EMPTY`. Replayed/forged ACK cannot ever create a native effect: all native actions return DENY.

`test_s1a_windows_native_scm_sid_impersonated_token_transport_denies_effects` now verifies **12 additional back-to-back full DENY responses** under a genuine low-privilege Windows client to catch repeat truncation. The existing **four native Windows selectors remain mandatory**; the first also must exercise on-wire repeated nonce and lowpriv access denial on the broker state, config and SQLite; #417 must re-pin the exact new SE source commit/blobs and rerun native Windows plus Linux. Portable ACK framing tests are not substitutes for real Windows proof.

### Bounded Windows I/O and crash/restart negative admission

All service `ConnectNamedPipe`, `ReadFile`, `WriteFile` and client read/write use Win32 **FILE_FLAG_OVERLAPPED**, bounded deadlines, kernel event waits, `CancelIoEx` and confirmation that the kernel completed/cancelled the operation before Python frees its OVERLAPPED event or buffer. If cancellation cannot drain within 1000 ms, the deny-only broker terminates (exit 86) rather than risk a use-after-free; no native effect is enabled. Whole server request deadline: **2 seconds** including slow-byte attacks. Connect is a stable 30-second overlapped pending listener (not a rapid instance teardown loop), with SCM STOP polled at most every 100 ms; on STOP or deadline the outstanding connect is cancelled and drained. Client I/O deadline is 4 seconds, WaitNamedPipe 2 seconds. SCM STOP never runs blocking pipe I/O in its control callback.

New **real native Windows required selectors**:

- `test_s1a_windows_native_stalled_peer_read_deadline_recovers` — separate unprivileged allowed SID opens a real named-pipe handle, sends no frame, waits >2 seconds, then proves a fresh valid IPC PING reaches the live service promptly; this requires actual Windows kernel overlapped cancellation.
- `test_s1a_windows_native_hard_crash_restart_rejects_old_epoch` — #417 operator MUST first record old epoch + real SCM PID, then **hard-kill that exact service PID**, restart the same SCM service and separately confirm new RUNNING PID. Provide `SBX3_S1A_TEST_PREVIOUS_EPOCH` and `SBX3_S1A_TEST_PREVIOUS_SERVICE_PID`. Test denies old epoch, confirms changed epoch and fresh authenticated transport, with every native effect still DENY.
- `test_s1a_linux_native_hard_crash_restart_rejects_old_epoch` — Linux operator must hard-kill old broker-UID service, restart under same broker-only state directory, and set the two `PREVIOUS_*` vars plus `SBX3_S1A_TEST_CURRENT_SERVICE_PID`. Test reads real `/proc/PID/status` kernel UID and process cmdline, denies old epoch and all effects.

#417 CI must execute the above **in addition to** the earlier platform-specific selector pair, under `SBX3_S1A_NATIVE_REQUIRED=1`. The tests do **not** themselves forge a kill or elevate an unprivileged client; CI operator is responsible for actual process hard crash, documented PID/epoch readback and service recovery. Missing operator evidence/env means HARD RED rather than skip. Required real native selector inventory: **Linux = 3**, **Windows = 4**. Portable = 6 (includes a static Windows ACL mask guard and the incomplete Linux socketpair deadline check; neither is native OS-positive).

Unproven until targeted runs: Windows SCM process readiness and LocalService state ACLs, real Windows kernel overlapped cancellation, Linux/Windows cross-process service restart, concurrency/nonce fault cuts, adversarial symlink/ACL swaps, malformed Windows frame and remote pipe probes. Current source/test additions are **not** security FINAL; no positive OS containment or native command authority is claimed. Any CI missing real OS attestations remains HOLD.

## Outstanding release blockers

CRT #156: identify a trusted server-only, durable owner/execution/sandbox/generation/profile/invocation/toolcall record and versioned revision/expiry/revoke registration, or return `HOLD_EXTRA_PATH` before introducing an authorizing API. The currently committed S1-A registration is intentionally **non-authorizing**; no semantic bridge to CRT/AE is implemented. #417 two-OS transport OS evidence must be re-run on every new exact source/workflow SHA; old raw logs cannot prove changed code. Follow-on S1-B isolation, S1-C kernel tree reattach, S1-D receipt and two-store CAS, Tools T1/Agent native effect routing remain separate HOLD. Source or CI merge is **not authorized**.
