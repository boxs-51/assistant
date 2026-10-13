# CL/UI #242 × AE #359 — absolute-deadline and durable-effect fencing contract (NEGATIVE/PROPOSAL)

**Stage:** design + negative test evidence only. **Authority:** NOT RELEASED. **Merge:** NO.
**Baseline:** Draft PR #501 @ `bd40a0e68e828dea7c93d93262b4c129fa60165c` → child Draft PR #507 @ `222273c010f6e44eb805520f789fea08b0afd4e8`.
**Ownership:** CL/UI owns the WebView prompt and client timeout; AE/SE owns server invocation admission and effect claim; client dispatcher owns authenticated installation/principal and canonical fingerprint; #359 controls durable consent/receipt integration.

## Verified source facts (main + child #507)

1. `cl/src/core/gateway_client.py::GatewayLLMClient.execute_capability` performs one POST containing the supplied `payload`. `UIBridge.execute_tool` supplies only `{"arguments": ...}`, **no stable trusted invocation ID**. PR #507 adds Requests `(connect=3.05, read=30.0)` **socket-inactivity** timeout and raises `GatewayEffectOutcomeUnknown` on transport corruption/loss.
2. `se/src/domain/schemas/capability.py::CapabilityExecutionRequest` declares `invocation_id: str | None` (optional). `se/src/transport/gateway/api/v1/capability_router.py::execute_capability` forwards it to the capability runtime, with authenticated `Identity`. Optional invocation fields are **not** proof of durable no-replay admission for direct UI actions.
3. `cl/src/ui/bridge.py::execute_tool` holds `_auth_transition_lock` from pre-effect auth-generation check through the blocking gateway POST. Releasing the lock before server admission is fenced reopens cross-principal effects.
4. PR #507's `OUTCOME_UNKNOWN`/ `retryable:false` is **a user-facing classification**, not a server readthrough token or a durable prohibition on a new/manual request.
5. `cl/src/core/client_invocation_ledger.py` and dormant #359 PR #482 are existing authorities for separate workflows. They are **not connected** to this direct UI POST. No assertion is made that the server lacks *other* persistence; the missing proof is the specific authenticated direct-UI→server composition.

## Proposed authority and state contract — requires AE #359 ACK before production work

- **Issuer:** trusted server admission layer derives `principal_id` from authenticated identity and validates a registered `client_id`. `invocation_id` must be a stable, scoped identifier with server custody, cryptographically random token or equivalent trusted binding. The browser/model/tool arguments/reason string must **never** be trusted to assert an actor or capability fingerprint.
- **Canonical binding:** `(client_id, principal_id, invocation_id, capability_id, capability_version, canonical_arguments_fingerprint, auth_generation, expiry)`. Validate the complete tuple before any re-read, approval, or dispatch. A reused `invocation_id` with different semantics is `CONFLICT / DENY`, not a second command.
- **Atomic admission:** durable write lock/unique constraint/transaction in the effect-owning SQL database. One reservation per semantic request, coordinated with authenticated principal revoke. `effect_started` must commit **before** executing a possible side effect, without any time-of-check/time-of-use gap. Repeated or concurrent requests with the same ID may only read the existing terminal/UNKNOWN state.
- **Consent:** for HIGH/CRITICAL, #359's durable pending store requires an authenticated human on the current boot generation. After crash/restart, PENDING or pre-effect APPROVED must prompt again with a new approval token. DENIED, expired, revoked, and principal-switched tokens cannot authorize. An in-memory UI approval is not sufficient issuer authority.
- **Effect handoff:** server transitions `PREPARED → [HUMAN_PENDING → HUMAN_APPROVED] → EFFECT_STARTED → TERMINAL` as the effect-owning authority. If request exits/crashes/times out after `EFFECT_STARTED` with no terminal proof, preserve `OUTCOME_UNKNOWN_NONREPLAYABLE` indefinitely per retention policy. An application-side timeout cannot imply the remote effect was cancelled.
- **Cancellation and principal switch:** before `EFFECT_STARTED`, cancellation/revoke can atomically DENY. After `EFFECT_STARTED`, cancellation or disconnect cannot unlock or reissue effects; return terminal result if durable, otherwise UNKNOWN. Principal-switch acknowledgement may proceed without waiting for remote I/O only if **server-side fences** prove all old-principal *not-yet-started* effects cannot start later.
- **Absolute deadline:** requires bounded admission and I/O budgets *plus* a server-side fencing/quarantine and supervised effect lifecycle. Requests' socket read timeout is not a total budget. A detached client worker or `Future.result(timeout=...)` with still-running HTTP is **not safe** as a standalone fix.
- **Reconcile API:** read-only by authenticated typed invocation tuple: `TERMINAL`, `DENIED`, `PENDING`, `UNKNOWN_NONREPLAYABLE`; retries never execute. A *new* invocation is separately authorized and may be a new side effect; duplicate-semantic UX policy needs its own explicit design.

## Negative-first acceptance matrix

| Gate | Reproduce | Required observation |
|---|---|---|
| N1: network trickle | Real HTTP server sends body bytes more frequently than read timeout for longer than that timeout | Evidence of lack of wall-clock bound; no false PASS |
| N2: manual resubmit | Client sees UNKNOWN after first attempt, caller explicitly calls again | Existing client **does** send second POST; proof server admission needed |
| N3: late effect / logout | Request accepted before logout; server delays admission until after revoke | DENY before effect, or old invocation remains quarantined; no stale effect |
| N4: two workers | Concurrent same ID from separate OS processes | Exactly one durable effect claim and no second effect; conflict on altered fingerprint |
| N5: crash K1→K2 | Stop first OS process during PENDING, APPROVED and EFFECT_STARTED in separate cuts | New approval after pre-effect crash; effect-started UNKNOWN/nonreplay |
| N6: real database parity | SQLite FK OFF/ON as relevant; PostgreSQL 17 Linux and Windows | Same transaction/revoke serialization and crash semantics |
| N7: partial read | Response body truncated after server effect | UNKNOWN, not SAFE_TO_RETRY; same-ID re-read is read-only |
| N8: operator cancel | Cancel before vs after committed effect claim | Pre-effect DENY; after claim UNKNOWN; never silently retry |
| N9: browser provenance | JS supplies spoofed client/principal/generation/fingerprint | DENY; trusted server/dispatcher derives authority |
| N10: slow service | Gateway responds continuously but slowly; external tool continues after timeout | No claim of absolute deadline until effects are quiesced/fenced by owner authority |
| N11: deadlock ordering | Concurrent authorization switch, client dispatcher callback and effect claim | Bounded lock acquisition, deterministic order, no stale principal use |
| N12: restart/replay | Reissue same ID after hard process restart | Read-through only, never second effect |

**Evidence hierarchy:** new test file `cl/tests/test_cl_ui_gateway_deadline_negative.py` implements **N1** and **N2** as deliberately passing *negative witnesses*. Passing these tests means the gap is demonstrated, **NOT** a production release gate. N3–N12 remain unimplemented/HOLD until AE & SE owners agree on exact source scope and atomic effect authority.

## Requested bilateral decisions from AE #359

Return `AE_CL_DIRECT_EFFECT_FENCE_ACK=CONDITIONAL_DESIGN` or `HOLD`, including:
- trusted issuer and canonical stable ID generation location;
- exact repository paths + database transaction/lock-owner where admission, `EFFECT_STARTED`, revoke, and readthrough serialize;
- whether the current `/{capability_id}/execute` endpoint is admissible to carry this mechanism or needs a new staged API;
- who owns cross-process K1/K2 + real PostgreSQL17 Linux/Windows acceptance evidence;
- ordering with CL PR501/507, AE PR482/488 and any blocking source/CLAIM collision.

**Verdict:** `CLIENT_IDLE_TIMEOUT=PASS_OBSERVED_SCOPED`; `ABSOLUTE_DEADLINE=HOLD`; `DURABLE_EFFECT_FENCING=NOT_IMPLEMENTED_FOR_DIRECT_UI`; `PRODUCTION_MERGE=NO`.
