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

An operator-provisioned broker OS UID (different from both test client UIDs), non-shared, broker-owned protected state directory **0700**, and broker-owned IPC directory (example mode **0710** group `sbx3_s1a_ipc`) are required. The broker creates its own AF_UNIX socket with mode 0660. Allowed client group membership permits DAC connect, while the broker checks the **kernel SO_PEERCRED UID** against a specific unprivileged authorized UID; foreign user denied. Parent mode 0700 with unrelated client would incorrectly block intended IPC, and group-writable world directories are disallowed. State must never be under an untrusted sandbox root. Verify path inode/owner/mode; neither client may write state or socket parent.

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

The tracked native transport calls `CreateNamedPipeW` with `PIPE_REJECT_REMOTE_CLIENTS`, explicit non-Everyone/anonymous SDDL DACL (`SY`, actual service SID, specifically allowed client SID), calls `ImpersonateNamedPipeClient`, `OpenThreadToken`, `GetTokenInformation(TokenUser/TokenIntegrityLevel)`, checks **actual allowed SID + medium-or-higher integrity**, and always `RevertToSelf`/close handles. #417 must run an **unprivileged client SID genuinely different from the service SID**, plus foreign/remote/low-integrity denials; a single administrator client is insufficient.

No Windows filesystem ACL is asserted solely by Python `os.stat`; #417 must independently inspect ACL of broker-only state and service config and prove authorized/unauthorized access. A LocalService directory that is client-writable is RED.

## Strict executable test selectors (no skip/xfail)

File `se/tests/architecture/test_sbx3_s1a_authenticated_ipc.py` contains:

- Portable, **NOT OS-positive:** `test_s1a_portable_generation_aliases_fail_closed`, `test_s1a_portable_bounded_frame_and_zero_effects`
- Linux **MANDATORY real separate UID**: `test_s1a_linux_native_distinct_uid_so_peercred_transport_denies_effects`; `test_s1a_linux_native_alias_replay_stale_and_missing_socket_deny`
- Windows **MANDATORY native SCM+token**: `test_s1a_windows_native_scm_sid_impersonated_token_transport_denies_effects`; `test_s1a_windows_native_stale_epoch_and_no_service_fallback`

#417 must select only the named platform-specific two tests per OS for its positive-transport suite, add the portable tests if useful, fail on 0 collected/skip/xfail/failure, run same-head Architecture independently, collect JUnit, OS PID/UID/GID/SID/epoch/DACL evidence without leaked secrets, and stop/delete service in a finally block. Missing env var or wrong platform results in assertion failure, NOT SKIP. Environment variable names are defined by `_linux_setup`/`_windows_setup` in the test source. Dedicated CI file path only: `.github/workflows/sbx3-s1a-authenticated-ipc-evidence.yml` (CI #417); **not** added by SE #164.

Further mandatory integration negatives for later revision: genuine hard broker kill/restart and stale epoch, concurrent replay and revoke/read CAS, adversarial symlink/ACL swaps, malformed Windows frame and remote pipe probes. Current selectors do not certify all of these yet; do not claim complete security FINAL. Any CI missing real OS attestations remains HOLD.

## Outstanding release blockers

CRT #156: identify a trusted server-only, durable owner/execution/sandbox/generation/profile/invocation/toolcall record and versioned revision/expiry/revoke registration, or return `HOLD_EXTRA_PATH` before introducing an authorizing API. The currently committed S1-A registration is intentionally **non-authorizing**; no semantic bridge to CRT/AE is implemented. #417 two-OS transport OS evidence remains to be run; Windows service viability and token ABI need actual raw logs. Follow-on S1-B isolation, S1-C kernel tree reattach, S1-D receipt and two-store CAS, Tools T1/Agent native effect routing remain separate HOLD. Source or CI merge is **not authorized**.
