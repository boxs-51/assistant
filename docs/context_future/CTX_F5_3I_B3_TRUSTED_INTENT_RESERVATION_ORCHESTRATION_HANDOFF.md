# CTX-F5-3I-B3 — Trusted Promotion Intent + Reservation Orchestration Handoff

Primary authority: Issue #15 / CTX.
Canonical governance: Issue #85 v2.5.

## Status

~~~text
stage = CTX-F5-3I-B3
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
exact development baseline = ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12
baseline Architecture #1882 / 37021044586 = GREEN/GREEN
parent CTX-F5-3I-B2 = LANDED / CANONICAL / HEALTHY
parent B2 merge commit = e138db0db9dc7aa165f693af2c9cb2f8a70ef7ec
IW-2026-10-02-05 = COMPLETE / authorization consumed
R12-F3-A PR #202 = LANDED / CANONICAL / HEALTHY
IW-2026-10-02-06 = COMPLETE / authorization consumed
production PRE-CLAIM = HOLD pending independent B3 audit
production CLAIM = NONE
production branch = NONE
runtime/container/API wiring = CLOSED
Memory admission invocation = CLOSED
schema/migration delta = ZERO
production/runtime delta = ZERO
merge authority = NONE
~~~

B3 freezes the server-owned handoff from the landed B2 trusted
TOOL_RESPONSE_PAYLOAD material to one exact MemoryPromotionIntent and the
already-landed durable reservation issuer.

B3 contract/evidence changes no production code and does not open an admission,
runtime, API, retrieval, or model-visible path.

## 1. Canonical components now available

The canonical repository already contains:

- DurableToolResponsePayloadSourceAuthority.read_trusted_promotion_material(...);
- TrustedToolResponsePromotionMaterial with:
  - one SourcePromotionProof;
  - detached canonical content_snapshot;
  - exact content_digest;
- MemoryPromotionIntent;
- MEMORY_SCHEMA_VERSION;
- DurablePromotionReservationIssuer.reserve(*, intent);
- DurableMemoryPromotionAdmission.admit(*, reservation, content).

What does not yet exist is one production server-owned orchestration boundary:

~~~text
validated source_ref + owner
-> B2 trusted material
-> exact server-constructed MemoryPromotionIntent
-> durable reservation issuer
~~~

Caller content, digest, proof, metadata, schema version, or owner must not fill
that authority gap.

## 2. Narrow first intent policy

A future B3 production implementation may construct exactly one intent policy:

~~~python
MemoryPromotionIntent(
    owner_user_id=material.source_proof.source_ref_snapshot.owner_user_id,
    source_ref_snapshot=material.source_proof.source_ref_snapshot,
    source_proof=material.source_proof,
    content_digest=material.content_digest,
    metadata={},
    memory_schema_version=MEMORY_SCHEMA_VERSION,
)
~~~

The first slice deliberately freezes:

~~~text
metadata = {}
memory_schema_version = canonical MEMORY_SCHEMA_VERSION
~~~

Caller-supplied owner, source snapshot, proof, digest, metadata, or schema version
is not intent authority.

Metadata enrichment and alternate schema policy remain CLOSED.

## 3. Same-call material authority

This stage closes the previously recorded:

FUTURE-FENCE-CTX-B2-MATERIAL-TO-RESERVATION-LIVENESS-1

at contract level with these semantics:

1. a successful B2 trusted-material read is point-in-time server authority for
   that same in-process orchestration attempt;
2. the returned material is not a durable reservation;
3. the returned material is not an Agent/R11/R12 retention pin or live root;
4. source disappearance after the successful B2 read does not retroactively
   mutate the already-returned detached material inside that same attempt;
5. B3 must not lock, pin, or extend Agent source retention merely to bridge the
   interval to reservation issuance;
6. if durable reservation issuance succeeds and commits, subsequent reservation
   authority belongs entirely to the existing reservation subsystem;
7. if cancellation, process loss, or failure occurs before durable reservation
   commit, no durable B3 authority exists;
8. a later retry after such pre-commit failure must start from a fresh B2
   trusted-material read against then-live durable source evidence;
9. cached/caller/process-memory copies of an earlier B2 material object are not
   replay authority across failed orchestration attempts.

Therefore:

~~~text
B2 material != durable reservation
B2 material != retention pin
B2 material != cross-process replay token
~~~

## 4. Existing issuer remains sole reservation authority

The future B3 seam may call only:

DurablePromotionReservationIssuer.reserve(intent=exact_intent)

for durable issuance.

B3 must not:
- generate or persist reservation rows directly;
- duplicate reservation-id convergence;
- duplicate proof-tuple uniqueness;
- duplicate exact-intent canonicalization;
- reinterpret ISSUED / CONSUMED / REVOKED;
- commit a second reservation transaction.

Issuer/repository authority remains canonical and external to the B3
orchestration seam.

## 5. Reservation does not persist source content

This is a concrete current-code fact and a mandatory future boundary.

The durable promotion reservation stores exact intent authority, including:
- owner;
- source snapshot;
- source proof;
- content_digest;
- metadata;
- memory_schema_version;
- canonical intent bytes/digest;
- proof tuple;
- reservation state.

It does not durably store the B2 content_snapshot.

Current admission separately requires:

~~~python
DurableMemoryPromotionAdmission.admit(
    *,
    reservation: PromotionReservation,
    content: Any,
)
~~~

and validates the canonical digest of that supplied content against
durable.intent.content_digest.

Therefore a durable reservation alone is insufficient to reconstruct exact
Memory content after process loss.

## 6. Reservation-to-admission recovery fence

Record and freeze:

FUTURE-FENCE-CTX-B3-RESERVATION-TO-ADMISSION-CONTENT-RECOVERY-1

Current contract disposition:

~~~text
fence status = OPEN / MUST BE DECIDED BEFORE RUNTIME OR ADMISSION ORCHESTRATION
B3 contract preparation = ALLOWED
B3 production PRE-CLAIM = HOLD pending independent decision
Memory admission invocation = CLOSED
public/runtime orchestration = CLOSED
~~~

Independent audit must decide whether the first B3 production slice may safely
end at:

~~~text
trusted material -> exact intent -> durable reservation
~~~

while returning detached content only as transient same-call material, or
whether crash-safe content authority requires a separate durable handoff/source
re-proof design before any B3 production service may be released.

No contract wording may silently treat the reservation as if it stores content.

## 7. Cancellation and failure ownership

B3 must not swallow asyncio.CancelledError.

Before issuer invocation:
- B2 source rejected/unavailable errors remain B2 errors;
- no reservation is synthesized.

During issuer invocation:
- MemoryPromotionIntent integrity errors remain canonical promotion primitive
  errors;
- reservation repository/issuer errors remain canonical reservation errors;
- a failed issuer call is not converted into successful reservation authority.

No best-effort reconstruction from local process state is permitted.

## 8. Explicit non-scope

B3 does NOT:
- change B2 source authority;
- change TrustedToolResponsePromotionMaterial;
- change MemoryPromotionIntent primitive semantics;
- change MEMORY_SCHEMA_VERSION;
- change reservation schema/repository/state machine;
- change DurablePromotionReservationIssuer;
- invoke DurableMemoryPromotionAdmission.admit(...);
- persist content in reservation rows;
- expose HTTP/public/model-callable promotion;
- add runtime/container wiring;
- create automatic promotion;
- inject Memory into ContextBuilder/model Working Set;
- open retrieval/search/ranking/vector/index/chunk/embedding;
- acquire Agent/R11/R12 retention/GC authority;
- acquire CAS lifecycle/dereference authority;
- acquire UBQ quota authority.

## 9. Cross-track guard

### AE-R12-F3-A / PR #202

Current refreshed canonical state:
- PR #202 is MERGED / SQUASH through IW-2026-10-02-06;
- canonical main = ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12;
- post-merge Architecture #1882 / 37021044586 = GREEN/GREEN;
- exact landed #202 delta remains the audited 13-path R12-F3-A scope;
- exact changed-path overlap with B3 contract candidate is zero.

R12 owns recovery/lease/Agent projection authority.

Any landing that modifies:
- successful COMMITTED AgentToolResult content/immutability;
- persisted execution/invocation/tool-call lineage used by B2;
- source liveness/retention semantics;
- canonical owner identity

is MATERIAL to B3 and requires fresh audit.

Exact post-landing inspection classifies PR #202 -> this B3 contract candidate
as NON_MATERIAL:
- no AgentToolResultRecord schema/Alembic change;
- no B2 trusted-material service change;
- no ContextSourceRef or ToolResponsePayload primitive change;
- no MemoryPromotionIntent, reservation issuer/repository, or Memory admission change;
- successful COMMITTED result identity/content semantics consumed by B2 remain intact.

Future R12 landings that touch the listed source/lineage/owner/liveness seams remain
MATERIAL-POTENTIAL / REFRESH-ON-LAND.

### UBQ / CAS / #156 / R11

Fresh cross-track refresh on current main:
- UBQ-5 / Issue #147 T-0 remains contract/evidence-only; future UBQ-5C production
  remains MATERIAL/HOLD against landed R12-F3-A seams;
- CAS / Issue #74 has no next production stage released and retains asset/provider
  lifecycle and destructive-GC authority;
- R11 / Issue #31 is COMPLETE; its retention/GC boundaries remain external;
- Issue #156 has no newer routing/fingerprint production change affecting the
  invocation/capability lineage consumed by B2.

B3 acquires no quota, asset lifecycle, routing/fingerprint, or destructive-GC
authority.

Embedded asset/provider-looking values remain opaque JSON.

## 10. Exact contract candidate scope

CTX-F5-3I-B3 contract candidate is exactly two files:

1. docs/context_future/CTX_F5_3I_B3_TRUSTED_INTENT_RESERVATION_ORCHESTRATION_HANDOFF.md
2. se/tests/architecture/test_ctx_f5_3i_b3_trusted_intent_reservation_orchestration_handoff.py

No se/src/** production file changes.

Architecture evidence must prove from canonical code that:
1. B2 exposes trusted proof + content snapshot + digest;
2. MemoryPromotionIntent requires owner/source/proof/digest/metadata/schema;
3. MEMORY_SCHEMA_VERSION is canonical;
4. DurablePromotionReservationIssuer accepts only exact intent;
5. durable reservation records persist intent/digest/proof/state but not source content;
6. DurableMemoryPromotionAdmission requires explicit content separately;
7. admission compares supplied content digest to durable intent digest;
8. runtime/container/API/admission orchestration remains absent from this candidate;
9. production/runtime/schema/migration delta is zero.

## 11. Independent audit questions

The independent auditor must answer:

1. Are same-call point-in-time B2 material semantics valid without retention
   ownership?
2. Is fresh B2 re-proof after pre-reservation crash/failure the correct retry
   boundary?
3. Is metadata={} + MEMORY_SCHEMA_VERSION the narrowest safe first server intent
   policy?
4. May a production B3 slice end immediately after durable reservation issuance
   while returning detached content only transiently in the same call?
5. Does FUTURE-FENCE-CTX-B3-RESERVATION-TO-ADMISSION-CONTENT-RECOVERY-1 block B3
   production PRE-CLAIM entirely, or only a later reservation-to-admission/runtime
   slice?
6. What exact production file/evidence scope may be released?
7. Does the exact landed R12-F3-A delta remain NON_MATERIAL to B3 after
   independent verification on this refreshed baseline?

## 12. Exit gate

This contract/evidence candidate may reach FINAL GREEN only when:
- changed files remain exactly the two files above;
- exact-head Linux + Windows Architecture are GREEN/GREEN;
- no blocking P0/P1/P2 remains;
- current-main/dependency drift is refreshed;
- independent audit accepts or repairs the authority boundary.

Even after contract landing:

~~~text
B3 production PRE-CLAIM = SEPARATE / REQUIRED
B3 production CLAIM = NONE
runtime/container/API/admission/retrieval authority = CLOSED
production merge authority = NONE
~~~

## Non-authority statement

CTX-F5-3I-B3 is a contract and architecture-evidence slice only.

It issues no reservation, admits no Memory, persists no source content, opens no
runtime path, changes no source retention, and grants no production merge
authority.
