# SBX3 B0 Native Broker and AE Durable Receipt Integration Plan

Status: DESIGN / TEST-FIRST / NOT ACTIVATED (2026-10-12).
Owners: SE #164, Tools #371, AE #359, CRT #156, CI #417.
This document grants NO native command, cleanup, production, merge, or audit authority.

## 1. Immutable dependency and branch topology

| Parent | Head | Actual contents |
| --- | --- | --- |
| SE #477 | 3e0bf347863fe02253b72ad9372ed80133408570 | Invocation authority; always pre-spawn DENY |
| SE #480 | 3a8e6f17381929137f290f70d22681ca182075a4 | Generation revocation and tests, exactly two MOD |
| SE evidence #478 | fb537f4fe1e66c4adf227ac56e4baded292165d0 | Two test/workflow ADD, sibling of #480, NOT inheriting #480 |
| Tools #474 | 94a4151b19796bfe287eb6dfbd96adc1551ff464 | Private adapter and negative tests, production module blob ce593df83b76ec4378c693591d06ff446ce13bd1 |
| AE #464 | 2fccc0d5f5ab1765428ea9da337563c3d6329a58 | PostgreSQL17 Task/HITL hard-restart tests, NOT native receipt |
| Historical D2 #465 | 41b46411d8f4d3702309c236d3b00618a51ad6d1 | Older conflicting SE/AE implementation, NEVER wholesale cherry-pick |

This PLAN branch starts from SE #480 and changes exactly one documentation path.
Do not mix #478's two evidence files into the #480 source claim, rewrite #474,
or imply that an evidence-only overlay constitutes a merged integration source.
Pin exact source hashes at every future child stage and re-anchor tests on change.

## 2. P0 generation revocation gap — before further positive integration

Confirmed source at #480: revoke_generation() serializes with SQLite BEGIN IMMEDIATE
and burns currently ISSUED tokens plus RESERVED attempts in one transaction.
However mint_diagnostic() INSERTs a new authority+attempt for a different
tool_call_id without checking a durable revoked-generation tombstone.
Therefore a fresh tool_call in a previously revoked generation appears mintable.
The existing native port STILL DENIES OS spawn. This is an authority-lifecycle
gap, NOT evidence of an exploitable native execution permission.

SE #164 next required scope:
- Persist a revoke/tombstone key: owner, execution, sandbox, generation, epoch,
  including an empty-generation revocation with zero existing tokens.
- Mint, preflight, reservation and revoke must check that key under the SAME
  authoritative cross-process transaction and permanent no-reuse rules.
- On revoked generation, mint with a fresh tool_call and invocation must DENY
  before insertion; restart must not erase tombstone or re-authorize.
- Separate unaffected generation remains usable for negative pre-spawn testing.
- Two OS processes racing mint/revoke, crash between writes, rollback,
  stale credential, expiry and SQLite WAL/FULL must be tested on Linux/Windows.
- Gate S0 FAILS until tests asserting fresh post-revoke mint DENIAL pass
  without xfail/skip. No revision of #480 itself from this plan branch.

Current exact-head #480 evidence: Native #38252581659, Linux 42 PASS +
Windows 42 PASS; Architecture #38252581655, Linux 3799 pass/22 skip/160
subtests, Windows Server 58 pass+12 subtests, Windows Client 264 pass.
All five jobs terminal SUCCESS, but the S0 test above is not yet present.

## 3. SE native broker S1 — authenticated receipt contract

Privileged OS-owned issuer/broker (NOT a Python protocol, signed-looking JSON,
PID or Tool result) must bind: issuer OS principal; owner_id, execution_id,
sandbox_id, lease_generation and broker epoch; invocation_id and tool_call_id;
policy/profile digest; deadline; OS-owned process tree reference and native
ledger revision. A private readthrough service must check those immutable
identities plus actual kernel descendant state and no-new-spawn generation fence.

Required states include RESERVED, DISPATCHING, CLOSING, VERIFIED_EMPTY,
OUTCOME_UNKNOWN and DENIED. On error, stale/reforged/revoked authority, missing
children, wrong generation, broker outage, kernel reattachment failure,
cancellation or crash -> nonretryable OUTCOME_UNKNOWN and root quarantine.
VERIFIED_EMPTY means an OS tree is empty, NOT that command effects are
idempotent or safe to retry.

Linux positive gate: privileged pre-first-instruction isolation (process,
filesystem and network NONE), cgroup2 identity, stopped-child admission,
descendants/grandchildren, crash reattach, atomic spawn/cleanup barrier.
Windows positive gate: actual Job Object and AppContainer/LPAC restricted token
with NETWORK NONE verified by a native kernel-level test; otherwise DENY before
spawn. Never release manager root on Python bool or fake receipt.

Existing NativeTerminalB0FailClosedPort.run_preconfined ALWAYS pre-spawn DENIES,
and verify_receipt ALWAYS False. Do not replace this default during design.

## 4. Tools #371 T1 and CRT #156 C1

Tools private TerminalTool._run_broker_owned remains hidden from public
execute/run/launch and model metadata. No host Popen fallback; preserve
trusted DIRECT behavior separately, and bounded stdout/stderr. A broker
exception, stale authority, timeout with unknown effects, invalid receipt or
cancelled task never becomes model-visible success or an automatic replay.
Re-run 52 source-pinned Tools negative tests per OS and #478 cross-PR pin
checks when any module/test/workflow blob changes.

CRT Agent model-origin terminal.run requires SANDBOX resource matching
execution_id, trusted owner and FallbackPolicy.NONE. terminal.launch and
python.run remain DENIED. A future native candidate Tools ok=True cannot
make CapabilityInvocation COMPLETED until SE broker proof is authenticated.
Add negative tests for direct runtime bypass, continuation and crash recovery.

## 5. AE #359 A1: durable write, load and recovery receipt fencing

Current-main source sites inspected:
- se/src/runtimes/agent/runtime.py: _persist_tool_result ~1254,
  _load_committed_tool_result ~572, _load_committed_tool_result_by_id ~820;
  ordinary persist ~2736, resumed persist ~677, continued persist ~985.
- se/src/runtimes/agent/persistence.py: save_tool_result ~2983,
  load_committed_tool_result ~3074, load_tool_result ~3068.
- se/src/runtimes/agent/recovery_execution.py: committed read/reuse and
  _project_continuation_result.
- se/src/runtimes/agent/resume_planning.py: _require_committed_projection,
  REUSE_COMMITTED; fork/transcript projection must be fenced too.

Explicit observed hazards in current persistence:
  * missing invocation repository compatibility path commits local results;
  * invocation with remote_outcome_state None commits local results;
  * preexisting COMMITTED record has early-return read path.
These are legitimate for other tools, but must NOT bypass native terminal
positive proof, even if model/user metadata claims verified_empty.

AE owner should implement a trusted SE-injected NativeTerminalReceiptVerifier,
not an AE-issued token or model JSON object, before any native successful
COMMITTED persistence (normal/resumed/continued/recovered) AND on every
COMMITTED native result read-through/restart/reuse. Bind issuer, owner,
execution, sandbox, generation, invocation, tool_call, tree ref, receipt
revision and broker epoch. Read from privileged SE native broker. Never trust
row commit_state alone; preserve existing nonprocess tool semantics.

TOCTOU: generation revoke and AE SQL COMMITTED write MUST be fenced by an
authoritative epoch/expected-revision CAS protocol. If OS broker and AE SQL
cannot be made transactionally consistent, leave result PROVISIONAL or
OUTCOME_UNKNOWN; do not pretend SQLite/PG and OS state share one transaction.
Broker unavailable, stale authority, identity mismatch, lost descendants or
old COMMITTED row without a genuine receipt -> no model success, no
effectful auto-retry. Pending HITL/TaskBudget/UBQ flows must not regress.

AE-owned child branch should adapt only the relevant four path ideas from
historical #465 by exact hunk review; DO NOT cherry-pick its older SE
sandbox manager/native recovery code over current #164 security stack.

## 6. Ordered independent implementation stages

| Stage | Owner | Separate future branch | Exit gate |
| --- | --- | --- | --- |
| S0 | #164 | Child of #480, exact SE authority/test files | Permanent revoke tombstone + fresh post-revoke mint denied; 2-OS tests |
| S1 | #164 + #417 | Separate native-issuer/OS broker branch | Real privileged kernel + durable readthrough; Windows native or explicit pre-spawn deny |
| T1 | #371 | Child of #474 | Private adapter remains unforgeable-from-model, no host fallback; 2-OS negatives |
| A1 | #359 | Dedicated AE-only branch on consciously pinned integration base | Before COMMITTED AND read/recovery native receipt checks, PG17 Linux/Windows + SQLite multi-process |
| C1 | #156 | CRT-only child after S1/A1 ABI freeze | Agent SANDBOX + NONE; before COMPLETED; unchanged DIRECT |
| E1 | #417 | Test/workflow-only exact-source pin | Real cgroup2 kernel descendants, Windows native gate, restart/revoke/read-through, no skip |
| FINAL | Independent auditor | No implicit production activation | Only owner-authorized merge after all technical gates |

A child branch from #480 is NOT itself the SE #478 cross-PR evidence branch.
Future integrated test runner should overlay precisely versioned children in
a disposable checkout, then freeze a reviewed composed source manifest.

## 7. Negative matrix and STOP conditions

- Revoke an empty generation; mint a new invocation/tool_call afterwards;
  concurrent revoke/mint across two independent OS processes; restart and
  durable ledger tombstone; wrong owner or generation; token expiry.
- Forge signed-looking receipt/JSON, change invocation/tool_call, reuse stale
  broker epoch, lose broker after purported success, crash before tree empty.
- AE persist ordinary/resumed/continued a fake successful native outcome:
  SQL row must not become COMMITTED; recovery/load/by-id/fork must not reuse.
- Revoke vs COMMITTED multi-worker race; DB lock timeout and rollback;
  PostgreSQL17 and SQLite WAL/FULL; parallel HITL/TaskBudget/UBQ neutrality.
- Linux real stopped child+cgroup descendant tree and root deletion fence;
  Windows Job/LPAC network NONE genuinely native, otherwise pre-spawn DENY.
- Agent terminal.run, terminal.launch, python.run stay fail-closed through
  direct runtime, continuation, restart and model-visible output.

STOP if any positive native result without privileged broker receipt, fresh
mint after revoked generation, nonreplayable UNKNOWN retried, root deletion
without OS fence, Windows egress unproven, CI RED or source SHA drift.
PRs #474/#478/#480 remain DRAFT and UNMERGED. This plan is not a release.
