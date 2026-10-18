# AE-R14-E P1B — Durable human consent, crash/restart authority boundary

**Status:** E-P1B-C0 candidate / fail-closed state machine / **not yet integrated** with active client UI, dispatcher, Agent/ResumeClaim or production side-effect path. Do not represent this stage as global R14-E P0 exit.

## Problem verified on main

- `cl/src/hitl/hitl_manager.py`: synchronous bool callback with no durable pending identity, restart generation or signed authorization.
- `cl/src/ui/hitl.py`: `_pending_approvals` in memory with a random approval ID; process death discards the pending prompt. A stale UI response cannot be trusted after restart.
- `cl/src/core/client_invocation_ledger.py`: durable PREPARED before target; RUNNING is set before target. RUNNING after a crash must conservatively remain UNKNOWN for NON_IDEMPOTENT work. No code in this stage relaxes that safety rule.

## Newly implemented dormant consent store

`cl/src/hitl/durable_pending_consent.py` uses SQLite `BEGIN IMMEDIATE` transactions and an immutable **trusted** (not caller/model-supplied) `client_id/principal_id/invocation_id/capability_id/request_fingerprint` binding. Risk is HIGH/CRITICAL. One bounded random approval token is authorized only in the issuing boot generation, bound to expiry.

| State before crash | Restart behavior |
| --- | --- |
| PENDING | New process must explicitly reissue a new approval ID for the *same* trusted invocation binding; old ID is rejected and a new human response required |
| APPROVED but no effect claim | Requires reissue and fresh human decision; no silent promotion |
| DENIED | Immutable denial; no new approval or target execution |
| EFFECT_STARTED | Always nonreplayable `OUTCOME_UNKNOWN`; never reissue or retry |
| EXPIRED | Fail closed, no renewal from stale request |

`claim_effect` is a **durable write-ahead effect-start barrier**: succeeds once only for an unexpired, correctly bound, current-boot APPROVED consent; must commit before invoking any side-effecting callable. SQLite alone does not provide signed proof that a UI actor is a human and does not itself guarantee OS-authenticated principal identity. The actual UI layer and dispatcher still have to bind those values from trusted authenticated runtime context.

## Test scope

`cl/tests/test_durable_hitl_pending_restart.py` runs independent Python child processes and calls `os._exit(79)` without graceful shutdown after PENDING, APPROVED, DENIED and EFFECT_STARTED writes. On fresh process instance it asserts:
- Old token is invalid, PENDING and previous APPROVED are reissued only after an explicit same-binding request and become PENDING again
- Wrong principal/fingerprint fails, double decisions are rejected; two simultaneous UI responses produce exactly one winner
- DENIED cannot be resurrected, EXPIRED cannot be recovered, EFFECT_STARTED cannot be replayed
- Calling `claim_effect` twice does not grant a second external effect
- No bypass via low/medium risk or implicit consent

`.github/workflows/ae-r14-e-p1b-durable-pending.yml` selects the exact pytest module on native Ubuntu 24.04 and Windows 2025, requires 0 failures/errors/skips and uploads a bounded evidence directory. Existing production callback wiring is deliberately unchanged.

## Must happen before E-P1B-PRODUCTION FINAL

1. **CL runtime + UI trusted adapter owner:** propagate an authenticated principal/client ID and immutable invocation/fingerprint (not a free-form provenance string) through `LocalCapabilityExecutor`, `HITLManager`, `UIBridge` and UI response; include generation and expiry in the display/response, with a secure principal reset and UI ownership check.
2. **Crash-safe connection to client invocation ledger:** only PREPARED, definitively pre-effect work may be re-prompted across K1→K2; after `mark_running` and `claim_effect` the original action must never reexecute on reconciliation. Persist terminal DENY before reporting to server.
3. **Server AE + E2E:** real S1 kill/S2 restart with pending user prompt and no CL consent response yet; K2 rebind/generation, stale response rejection, decision-then-effect gate, cancel/timeout/revoke and rival-K2 tests on actual PostgreSQL 17 Linux and native Windows.
4. **Independent negative review:** verify principal spoof, stale generation/token, client restart, concurrent responses and real native OS hard exits. Cross-track #242 CL/UI ownership must agree before production activation. Never infer UI authentication merely from stored approval state.

**No production activation, no bypass of fail-closed RUNNING state, no merge.**
