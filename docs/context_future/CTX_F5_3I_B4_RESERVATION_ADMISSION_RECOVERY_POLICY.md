# CTX-F5-3I-B4 — Reservation-to-Admission Recovery Policy

Primary authority: Issue #15 / CTX.
Canonical governance: Issue #85 v2.5.

## Status

~~~text
stage = CTX-F5-3I-B4
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
exact development baseline = bb5fc2b06b34dd4838805eff5f72c2d14e74cc68
parent CTX-F5-3I-B3 = LANDED / CANONICAL / HEALTHY
parent B3 merge commit = f577fb370f1a73a8fdcc69e4221ac41c925a0338
post-merge Architecture #1901 / 37088891913 = GREEN/GREEN
IW-2026-10-03-01 = COMPLETE / authorization consumed
production PRE-CLAIM = HOLD pending independent B4 audit
production CLAIM = NONE
production branch = NONE
Memory admission invocation = CLOSED
runtime/container/API wiring = CLOSED
durable content persistence = CLOSED
retention/pinning/GC = CLOSED
production/runtime/schema/migration delta = ZERO
merge authority = NONE
~~~

B4 freezes the recovery/liveness decision that must be made before the landed
B3 reservation-only handoff may be composed with the already-canonical atomic
Memory admission service.

B4 changes no production code and grants no runtime, API, admission, retention,
or durable-content authority.

Independent finding
P1-CTX-F5-3I-B4-CONSUMED-REPLAY-RESERVATION-RECOVERY-1
is repaired by this contract candidate through the terminal-state-aware split
below; independent replacement-head audit remains required before closure.

## 1. Canonical parent state

The canonical repository now contains all of the following independently
landed components:

- DurableToolResponsePayloadSourceAuthority.read_trusted_promotion_material(...);
- CTX-F5-3I-B3 DurableToolResponsePayloadPromotionOrchestration.reserve(...);
- MemoryPromotionIntent;
- DurablePromotionReservationIssuer.reserve(...);
- durable reservation persistence with exact intent/proof/digest/state;
- DurableMemoryPromotionAdmission.admit(*, reservation, content).

The landed B3 production seam ends at:

~~~text
fresh B2 trusted material
-> exact server-owned MemoryPromotionIntent
-> existing DurablePromotionReservationIssuer.reserve(...)
-> canonical PromotionReservation + transient same-attempt trusted material/content
~~~

It does not call admission.

## 2. Concrete content-recovery gap

DurablePromotionReservationRecord persists authority material:

~~~text
promotion_authority_id
exact MemoryPromotionIntent
intent digest / exact canonical bytes
source authority identity
proof_receipt_id
authority_state_token
MEMORY_PROMOTION scope
ISSUED / CONSUMED / REVOKED state
~~~

It does not persist source content.

Current admission requires explicit content:

~~~python
DurableMemoryPromotionAdmission.admit(
    *,
    reservation: PromotionReservation,
    content: Any,
) -> MemoryRecord
~~~

Admission canonicalizes that supplied content, computes its digest, loads the
durable reservation by exact promotion_authority_id, and requires:

~~~text
payload_digest == durable.intent.content_digest
~~~

Therefore:

~~~text
durable reservation alone != exact Memory content
durable reservation alone != crash-safe content recovery
~~~

## 3. TOOL_RESPONSE_PAYLOAD fresh re-proof is deterministic while source is live

For the currently supported TOOL_RESPONSE_PAYLOAD source, B2 reconstructs all
proof material from durable immutable source/result/lineage state.

The authority state token is a domain-separated SHA-256 hash of exact durable
identity/state/content material. The proof receipt is another domain-separated
SHA-256 hash over the reconstructed source identity plus that authority state
token.

Neither value is random, timestamp-minted, nor process-local.

Therefore, while the original durable source remains eligible and readable, a
fresh B2 read using the exact source_ref and owner reproduces:

- the same canonical source_ref snapshot;
- the same ToolResponsePayload identity;
- the same content digest;
- the same authority_state_token;
- the same proof_receipt_id;
- the same SourcePromotionProof;
- the same narrow B3 MemoryPromotionIntent.

For reservation issuance/retry, the existing reservation issuer may converge
that exact intent only when the durable winner is still ISSUED. The issuer
intentionally rejects terminal CONSUMED and REVOKED winners.

Therefore B4 separates two recovery authorities:

~~~text
issuance recovery
  = fresh B2 re-proof -> exact intent -> issuer create/converge only while ISSUED

admission replay recovery
  = fresh B2 re-proof -> exact intent
  -> trusted non-minting exact-reservation resolution
  -> recover exact PromotionReservation envelope for ISSUED or CONSUMED
  -> REVOKED fails closed
~~~

This is source-backed recovery. It is not reservation-only content recovery,
and terminal-state replay does not weaken the issuer contract.

## 4. Recovery cuts

### 4.1 Known failure before reservation commit

If reservation issuance is known not to have committed:

~~~text
retry
-> fresh B2 trusted-material read
-> rebuild exact server intent
-> existing issuer reserve(...)
~~~

Cached/process-memory B2 material is not retry authority.

### 4.2 Reservation commit may have succeeded but B3 response was lost

The retry must not assume that no reservation exists.

If source remains live, retry first rebuilds the exact intent and resolves
whether an exact durable winner already exists:

~~~text
fresh B2 re-proof
-> exact same intent
-> trusted exact-reservation resolution

no durable winner
  -> existing issuer may create/converge the ISSUED reservation

durable ISSUED winner
  -> recover exact envelope; issuer convergence remains valid for issuance retry

durable CONSUMED winner
  -> do NOT call issuer
  -> recover exact envelope through the non-minting replay-recovery path

durable REVOKED winner
  -> FAIL CLOSED
~~~

No replacement promotion authority may be minted to escape the ambiguous
outcome.

If source re-proof is no longer possible, the retry fails closed.

The existence of a durable reservation does not authorize synthetic content.

### 4.3 Process loss after reservation result but before admission

If the process loses the transient B3 content before admission:

- no cached material may be treated as durable replay authority;
- a later attempt may recover only through a fresh trusted source re-proof;
- the exact reconstructed intent must resolve to/match the durable reservation;
- if no durable winner exists, only the issuance path may call the issuer;
- if the durable winner is ISSUED or CONSUMED, a trusted non-minting resolver
  may reconstruct the exact PromotionReservation envelope from durable authority;
- REVOKED remains terminal and fails closed;
- only then can fresh transient content become candidate input to a separately
  released admission orchestration.

If source re-proof fails because the source disappeared, current repository
authority cannot reconstruct the missing content from the reservation alone.

### 4.4 Admission commit ambiguity

DurableMemoryPromotionAdmission already owns its own atomic replay rule:

~~~text
CONSUMED reservation + same exact PromotionReservation envelope
+ same exact content
-> exact durable Memory replay
~~~

The existing issuer is not a replay resolver:

~~~text
issuer + exact intent + durable ISSUED  -> returns/converges winner
issuer + exact intent + durable CONSUMED -> PromotionReservationAlreadyConsumedError
issuer + exact intent + durable REVOKED  -> PromotionReservationRevokedError
~~~

Therefore admission replay recovery must not call the issuer for a terminal
CONSUMED winner.

B4 freezes a future trusted, non-minting exact-reservation recovery authority:

~~~text
fresh B2 re-proof
-> rebuild exact server-owned MemoryPromotionIntent
-> resolve durable reservation by exact canonical intent/proof authority

no durable reservation
  -> only issuance recovery may call existing issuer

durable ISSUED
  -> verify exact canonical intent/proof equality
  -> recover exact PromotionReservation envelope
  -> later admission may perform first atomic admit

durable CONSUMED
  -> verify exact canonical intent/proof equality
  -> recover exact PromotionReservation envelope without minting
  -> later admission may execute its existing exact-content replay rule

durable REVOKED
  -> FAIL CLOSED
~~~

The resolver must mint no promotion_authority_id and must not accept a
caller-supplied promotion_authority_id as authority. Durable repository lookup
by exact intent/proof authority may be used to find the canonical winner, but
the reconstructed envelope is trusted only after exact canonical equality is
re-proved.

If state changes ISSUED -> CONSUMED after resolution and before admission, the
admission service remains authoritative: it reloads durable state and applies
its existing first-admit or exact replay rules.

A higher-level retry still needs fresh exact content. If process-local content
was lost, B4 requires fresh B2 source re-proof; it does not pretend that either
the reservation or promotion_authority_id can recover content by itself.

## 5. Proposed narrow first policy — fail-closed source-backed recovery

The owner proposes the following minimal policy for independent audit:

1. same-process transient B3 content may be consumed only by a later separately
   released admission orchestrator;
2. every cross-process retry starts from a fresh B2 source re-proof;
3. the reconstructed narrow server intent must be exact;
4. recovery must first resolve an exact durable winner by trusted canonical
   intent/proof authority, without minting;
5. no durable winner -> only the existing issuance path may call the issuer;
6. ISSUED winner -> recover its exact PromotionReservation envelope and allow a
   later admission attempt;
7. CONSUMED winner -> recover its exact PromotionReservation envelope without
   calling the issuer, so a later admission call can exercise exact replay;
8. REVOKED winner -> fail closed;
9. if the source remains live, recovery may continue using fresh transient
   content from that same B2 re-proof;
10. if the source is unavailable/rejected after process loss, recovery fails
    closed;
11. fail-closed recovery does not auto-revoke or auto-consume an existing
    reservation;
12. fail-closed recovery does not synthesize content;
13. fail-closed recovery does not create a replacement reservation to bypass the
    existing durable winner;
14. caller-supplied promotion_authority_id or content is never sufficient
    recovery authority;
15. CTX does not pin/extend Agent/R11/R12 source retention merely to guarantee
    eventual Memory promotion;
16. no durable content copy is added to reservation rows merely to guarantee
    eventual admission.

This policy intentionally chooses safety over guaranteed liveness after source
deletion.

## 6. Open fence disposition is not self-decided

The existing fence remains:

FUTURE-FENCE-CTX-B3-RESERVATION-TO-ADMISSION-CONTENT-RECOVERY-1

B4 owner proposal:

~~~text
proposed recovery = fresh source-backed re-proof + terminal-state-aware exact reservation resolution
issuance recovery = existing issuer only for no-winner/ISSUED cases
CONSUMED replay recovery = trusted non-minting exact-envelope recovery; issuer is NOT used
REVOKED = FAIL CLOSED
reservation-only content recovery = NOT ALLOWED
source unavailable after process loss = FAIL CLOSED
durable content persistence = NOT PROPOSED
source retention pin = NOT PROPOSED
~~~

Independent audit must decide whether this policy is sufficient to close the
fence for a later bounded reservation-to-admission orchestration PRE-CLAIM.

If the auditor determines that eventual/crash-safe admission must remain
recoverable after source deletion, then B4 must HOLD and a separate durable
content/handoff authority contract is required before admission orchestration.

B4 itself does not close the fence by owner assertion.

## 7. Safety versus liveness

The proposed policy guarantees no false authorization:

- no admission from a reservation without exact content;
- no admission from stale cached B2 material;
- no alternate content accepted because its digest happens to be caller supplied;
- no new promotion authority minted after ambiguous issuance;
- no source lifecycle authority acquired by CTX.

It does not guarantee successful retry after source deletion.

A stranded ISSUED reservation is a liveness/lifecycle condition, not permission
to fabricate content, consume the reservation, or change source retention.

Reservation lifecycle cleanup/revocation policy remains separately owned and is
not released by B4.

## 8. Explicitly prohibited shortcuts

B4 does NOT authorize:

- adding content/content_snapshot to promotion reservation rows;
- changing reservation model/schema/Alembic;
- adding a blob/object-store content side channel;
- pinning Agent tool-result rows or changing R11/R12 retention;
- treating promotion_authority_id as a content locator;
- reconstructing content from content_digest;
- accepting caller-supplied content as recovery authority without fresh trusted
  source proof;
- accepting caller-supplied promotion_authority_id as sufficient reservation
  recovery authority;
- weakening DurablePromotionReservationIssuer so CONSUMED or REVOKED becomes
  issuable/reusable;
- minting a new promotion_authority_id when an exact durable winner exists;
- auto-revoking an ISSUED reservation because source re-proof failed;
- invoking DurableMemoryPromotionAdmission.admit(...);
- adding runtime/container/API wiring;
- exposing public/model-callable promotion;
- changing CAS/UBQ/R11/R12/#156 authority.

## 9. Cross-track guard

### R12 / source lineage

R12 owns execution/recovery/lease authority. Any future movement that changes
successful COMMITTED tool-result identity/content, invocation/capability/session
lineage, owner binding, or source liveness/retention is MATERIAL to B4 recovery
and requires bilateral refresh.

R12-F3-B production CLAIM is now active on
work/ae-r12-f3b-production-bb5fc2b0, but no canonical F3-B production delta is
consumed by this B4 contract baseline. The CLAIM itself does not change B2/B4
source semantics. Any actual production movement affecting the lineage/liveness
conditions above is MATERIAL and requires bilateral refresh before B4
production PRE-CLAIM.

### CAS

CAS retains asset/provider lifecycle, dereference, destructive GC and provider
cleanup authority. B4 does not make embedded asset-like values into CAS
lifecycle authority.

### R11

Issue #31 is COMPLETE. R11 retention/GC boundaries remain frozen and external.
B4 does not reopen R11 or create a new source live root.

### UBQ

UBQ-5A/T-0 is canonical on this development baseline as an evidence-only
timeout contract. Its landing is semantically NON_MATERIAL to B4 and transfers
no Memory/content-recovery authority.

Timeout/quota migration does not authorize content recovery, reservation state
changes, source retention, or Memory admission.

### #156

Routing target/fingerprint/sandbox authority remains external. B4 depends only
on canonical invocation_id/capability_id lineage already consumed by B2.

## 10. Exact contract candidate scope

CTX-F5-3I-B4 contract candidate is exactly two files:

1. docs/context_future/CTX_F5_3I_B4_RESERVATION_ADMISSION_RECOVERY_POLICY.md
2. se/tests/architecture/test_ctx_f5_3i_b4_reservation_admission_recovery_policy.py

No se/src/** production file changes are part of this candidate.

Architecture evidence must prove current canonical facts:

1. B3 returns durable reservation plus transient trusted material;
2. B2 proof state/receipt are deterministic domain hashes of durable material;
3. durable reservation records contain exact intent/proof/state but no content;
4. repository can reconstruct durable reservation authority by exact intent and
   proof tuple without minting a new authority id;
5. existing issuer returns/converges only ISSUED and rejects CONSUMED/REVOKED;
6. admission requires a PromotionReservation envelope plus explicit content;
7. admission checks content digest against durable intent and supports exact
   CONSUMED replay;
8. B4 future recovery contract requires a trusted non-minting exact-envelope
   recovery path for ISSUED/CONSUMED and fail-closed REVOKED;
9. caller promotion_authority_id/content alone never becomes recovery authority;
10. B4 contract chooses no durable content persistence or retention pin;
11. B4 leaves production admission/runtime authority closed pending independent
   decision.

## 11. Independent audit questions

Independent auditor must answer:

1. Does fresh B2 re-proof reproduce exact proof/intent authority for an unchanged
   eligible TOOL_RESPONSE_PAYLOAD source?
2. Is issuer convergence the correct recovery mechanism after ambiguous
   reservation issuance while the source remains live?
3. Is fail-closed behavior after source deletion safe and acceptable for the
   first reservation-to-admission orchestration slice?
4. Does a stranded ISSUED reservation require lifecycle/revocation work before
   admission orchestration may be released, or may that cleanup remain later?
5. Is same-process use of B3 transient content sufficient authority for a later
   direct admission call?
6. Is cross-process fresh re-proof + trusted non-minting exact-reservation
   resolution sufficient for later ISSUED/CONSUMED retry without persisting
   content?
7. Is preserving issuer rejection of CONSUMED/REVOKED while adding a separate
   trusted exact-envelope recovery authority the correct terminal-state split?
8. Does the open recovery fence therefore close under this bounded policy, or
   is a durable content/handoff primitive mandatory first?
9. If the fence can close, what exact production/evidence file scope may be
   PRE-CLAIMED next?
10. Do current R12/UBQ/CAS/#156/R11 movements remain NON_MATERIAL to this
    contract?

## 12. Exit gate

This contract/evidence candidate may reach FINAL GREEN only when:

- changed files remain exactly the two files above;
- exact-head Linux + Windows Architecture are GREEN/GREEN;
- no blocking P0/P1/P2 remains;
- current-main/dependency drift is refreshed;
- independent audit decides the recovery fence.

Even after B4 contract landing:

~~~text
production reservation-to-admission PRE-CLAIM = SEPARATE / REQUIRED
production CLAIM = NONE
runtime/container/API authority = CLOSED
durable content persistence = CLOSED unless separately released
retention/pinning/GC authority = CLOSED
production merge authority = NONE
~~~

## Non-authority statement

CTX-F5-3I-B4 is a contract and architecture-evidence slice only.

It performs no admission, writes no Memory, changes no reservation state,
persists no content, pins no source, adds no runtime path, and grants no
production merge authority.
