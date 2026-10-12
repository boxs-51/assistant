# AE #359 A1 — Unactivated native receipt verifier and durable read/write fences

Status: negative-only A1 candidate; production native Agent remains DENY.
Source base: main af0feb268b59cc9828c536eda1629c3a64c39bed.
Ownership: AE #359. SE #164 continues to own native issuer/revoke/broker state;
CRT #156 owns BEFORE CapabilityInvocation COMPLETED; Tools #371 owns private
terminal adapter. This proposal does NOT cherry-pick obsolete PR #465 SE code.

## Existing source paths fenced

- AE DurableAgentStore save_tool_result — BEFORE any native successful
  COMMITTED write, including no-invocation compatibility, local outcome
  with no remote_outcome_state, and R6 terminal promotion. A preexisting
  native COMMITTED record cannot be confirmed/updated as success.
- DurableAgentStore load_committed_tool_result and load_tool_result — AFTER
  restart reject an old successful native COMMITTED record, plus a prospective
  PROVISIONAL->COMMITTED promotion. SQL rows remain intact for incident audit.
- AgentRuntime _persist_tool_result, _load_committed_tool_result and
  _load_committed_tool_result_by_id — no native result accepted for ordinary,
  resumed, continuation/model read, even if a synthetic durable store is used.
- AgentRecoveryExecutionService _load_committed_read_only and
  _project_continuation_result — no model-facing recovery of forged COMMITTED.
- AgentResumePlanningService _validate_active_batch_coverage and
  _require_committed_projection — REUSE_COMMITTED blocked.
- safe_point_reconstruction — both committed transcript sanitation and active
  batch reconstructed result shortcut require the same negative native gate.

Capability IDs presently classified as Agent-native processes are
terminal.run, terminal.launch and python.run. Non-native tool results and
FAILED/OUTCOME_UNKNOWN native outcomes remain representable. No path can
obtain positive verification from Agent output, metadata, PID, OS tree
VERIFIED_EMPTY, SQL commit_state, synthetically signed receipt, environment
variable or injected test callback.

## Contract for eventual SE readthrough (NOT IMPLEMENTED)

The contract placeholder NativeReceiptIdentity binds issuer OS principal,
owner_id, execution_id, sandbox_id, lease_generation, invocation_id,
tool_call_id, broker_epoch, native_ledger_revision and process_tree_ref.
It is a *lookup identity*, never a verification token. AE does not mint it.
SE must provide an OS-authenticated private readthrough, assert native
pre-first-instruction process/fs/network NONE isolation and same-generation
no-new-spawn fence, and verify durable broker terminality and descendant
cleanup before native SUCCESS can be considered.

SE #164 remains negative-only; current sources provide no production-approved
native receipt. Until #164 S0 tombstone and S1 broker are independently
verified, AE A1 always raises AE_NATIVE_RECEIPT_UNAVAILABLE for native SUCCESS.
Even after a positive broker exists, cross-store SQL COMMITTED vs SE revoke
requires an epoch/revision CAS or durable two-phase protocol. Merely calling
verify_receipt() before a SQL commit risks TOCTOU and must not release native
SUCCESS. CRT must separately keep CapabilityInvocation non-COMPLETED until
privileged proof; Agent A1 alone does not authorize CRT transition.

## Evidence and release gates

Tests:
- Native success denial for all three IDs and each write/read/recovery phase.
- Forged model metadata, old native COMMITTED SQL row, and real fresh Python
  interpreter after SQLite persistence: no native model success.
- Legacy local/pre-dispatch COMMITTED shortcut: native denied before SQL insert.
- Runtime and recovery mock store bypasses: native blocked.
- Non-native success and native failure/unknown: unaffected.
- Targeted real Linux and native Windows Python 3.12 runs with 0 skips; full
  Architecture Baseline must remain GREEN.

Further needed before any A1 *positive* stage: genuine SE S0 permanent
generation tombstone (including mint-after-revoke); OS-owned receipt
readthrough; broker/SQL cross-store atomicity or conservative PROVISIONAL/
OUTCOME_UNKNOWN; PostgreSQL17 write/restart tests on both OSes; competing
revoke-vs-COMMITTED and broker outage; independent non-author audit.

DO NOT enable Agent native, alter private SE/Tools/CRT owners' files or merge.
