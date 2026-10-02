# CTX-F5-3I-B2 — Trusted TOOL_RESPONSE_PAYLOAD Promotion Material Snapshot

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.

## Status

```text
stage = CTX-F5-3I-B2
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
exact development baseline = 1ceb34f561d73e26775910cd3468c962c17d61cd
baseline Architecture #1822 / 36994059445 = GREEN/GREEN
parent CTX-F5-3I-B1 = LANDED / CANONICAL / HEALTHY
parent B1 merge commit = ee94efc323425ed0016c307577ac0412e43ddea4
IW-2026-10-02-04 = COMPLETE / authorization consumed
selected source kind = TOOL_RESPONSE_PAYLOAD
production PRE-CLAIM = HOLD pending independent B2 audit
production CLAIM = NONE
production branch = NONE
runtime/container/API wiring = CLOSED
reservation/Memory admission = CLOSED
retrieval/ContextBuilder/model visibility = CLOSED
production/runtime/schema/migration delta = ZERO
merge authority = NONE
```

B2 freezes the missing trusted material boundary between the landed B1 source
re-proof and later MemoryPromotionIntent / durable admission work.

B2 itself changes no production code and releases no runtime path.

## 1. Exact authority gap

The landed B1 service:

`DurableToolResponsePayloadSourceAuthority.reprove_for_memory_promotion(...)`

returns only one `SourcePromotionProof`.

During the same durable read, B1 already reconstructs exact source authority from:

```text
AgentToolResultRecord
-> AgentExecutionRecord
-> canonical chat_data.Session
-> CapabilityInvocationRecord
-> AgentToolCallRecord
```

and computes:

```text
canonical_content = canonical_payload_bytes(result.output)
content_digest = sha256(canonical_content)
payload_id = tool_response_payload_id(..., content_digest=content_digest, ...)
```

But the returned public value does not contain either:
- a detached trusted content snapshot; or
- the exact trusted content digest as an independently consumable field.

Downstream promotion primitives require `MemoryPromotionIntent.content_digest`,
and durable admission later receives an explicit `content` value.

Therefore a later runtime may not fill this gap from caller-provided
`ToolResponsePayload.content`, caller-provided digest, or the process-local
`InMemoryToolResponsePayloadRepository`.

## 2. B2 trusted material result

A future B2 production implementation must expose one immutable trusted material
result for exactly `ContextSourceKind.TOOL_RESPONSE_PAYLOAD`, conceptually:

```python
TrustedToolResponsePromotionMaterial(
    source_proof: SourcePromotionProof,
    content_snapshot: <detached canonical JSON value>,
    content_digest: str,
)
```

The exact production type name and module placement remain subject to the later
production PRE-CLAIM. This contract freezes the semantics, not the final symbol.

All three returned fields must be derived from the same durable result and the
same source-authority read snapshot.

## 3. Exact authoritative content

Authoritative content is exactly:

`AgentToolResultRecord.output`

from the same durable row that passes the complete B1 eligibility and lineage
proof.

The future implementation must:

1. validate the supplied ContextSourceRef using the landed B1 rules;
2. load the exact B1 durable lineage;
3. require exact successful source eligibility:
   - commit_state == COMMITTED;
   - success is True;
   - error_code is None;
   - error_message is None;
   - retryable is False;
4. canonicalize exact `result.output` using existing CTX-F1
   `canonical_payload_bytes(...)`;
5. detach a canonical JSON snapshot from those canonical bytes;
6. compute the exact SHA-256 content digest;
7. reconstruct payload_id from the same immutable lineage and digest;
8. reconstruct the same trusted ContextSourceRef, authority_state_token, and
   proof_receipt_id semantics already landed in B1;
9. return proof + detached content snapshot + digest from this one verified
   material set.

## 4. Exact digest binding

The following equality is mandatory:

```text
B2.content_digest
== sha256(canonical_payload_bytes(B2.content_snapshot))
== digest used to reconstruct B1 payload_id
== content_digest bound into B1 authority_state_token material
```

A caller-supplied content value, caller-supplied digest, process-local payload
record, or later independent re-read is not a substitute for this exact binding.

The B2 snapshot must be detached before leaving the trusted read boundary so
later caller mutation cannot change the material.

## 5. One authority algorithm, not two

B2 must not fork the landed B1 proof algorithm.

Preferred later production shape:

```text
one internal trusted durable material builder/read path
-> B1 reprove_for_memory_promotion(...) returns material.source_proof only
-> B2 trusted-material reader returns the complete material
```

The existing B1 public protocol signature and behavior must remain compatible.

If a different implementation shape is proposed, the PRE-CLAIM must prove
byte/semantic equivalence and no independently drifting duplicate source-authority
algorithm.

## 6. Read-only session boundary

One trusted B2 material read must use one fresh injected AsyncSession context,
matching B1 ownership.

Allowed:
- B1-equivalent primary-key/select reads;
- canonical byte construction;
- detached JSON materialization before session exit.

Forbidden:
- commit;
- flush;
- rollback;
- row lock;
- repair/normalization write;
- source-result mutation;
- source pinning;
- retention extension;
- new shared Agent/R11/R12 repository ownership.

SQLAlchemy read/storage failures must fail closed through a bounded CTX
source-material unavailable surface.

`asyncio.CancelledError` must propagate unchanged.

## 7. Whole-payload only

Initial B2 authority is the whole canonical JSON value from exact
`AgentToolResultRecord.output`.

Still CLOSED:
- subset/field selection;
- summarization or transformation;
- chunking or embedding;
- semantic extraction;
- structural asset interpretation;
- CAS validation/hydration/dereference;
- provider file/object resolution.

Embedded asset/provider-looking values remain opaque JSON.

## 8. Retention / liveness boundary

Agent/R11/R12 remain source-liveness and retention owners.

Before a successful material read:

```text
missing / GC-collected / malformed / foreign / inconsistent durable source
=> fail closed
=> no trusted B2 material
=> no new promotion reservation
```

B2 creates no Agent live root and does not pin source history.

The already-frozen post-reservation GC semantics remain separate downstream
authority; B2 itself issues no reservation.

## 9. Downstream authority remains separate

B2 does not construct or persist a `MemoryPromotionIntent`.

B2 does not:
- call PromotionReservationIssuer.reserve(...);
- issue, verify, consume, or revoke a PromotionReservation;
- call DurableMemoryPromotionAdmission.admit(...);
- choose future caller metadata policy;
- expose HTTP/public/model-callable promotion;
- register runtime/container wiring;
- enable automatic promotion;
- create ContextSourceKind.MEMORY;
- open Memory retrieval/search/ranking/vector/index/chunk/embedding;
- inject Memory into ContextBuilder or model Working Set.

A future intent/orchestration stage must separately freeze how trusted B2
material becomes one exact MemoryPromotionIntent and how that intent is passed
to already-landed reservation/admission authority.

## 10. Cross-track authority fences

### UBQ / Issue #146

UBQ-4 is now LANDED / CANONICAL / HEALTHY at final Wave #200 main.

B2 performs no tool execution and no inference. It must not re-admit, recharge,
settle, or refund UBQ quota.

### AE-R12 / Issue #107

Current R12-F3 work is contract/evidence-only.

Any later production movement that changes persisted
AgentToolResult/AgentExecution/CapabilityInvocation/AgentToolCall identity,
result mutability, canonical owner lineage, or source retention/liveness is a
material B2 re-audit trigger.

B2 acquires no recovery, lease, continuation, or reconciliation authority.

### CAS / Issue #74

Embedded asset/provider-looking JSON remains opaque. B2 acquires no FileAsset,
FileBlob, ObjectStorage, provider hydration, lifecycle, deletion, or GC
authority.

### Agent-only / Issue #156

Canonical invocation_id/capability_id meaning must remain preserved.
Target/implementation/connection/routing/fingerprint/sandbox authority remains
external.

```text
CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE
```

## 11. Exact contract candidate scope

CTX-F5-3I-B2 contract candidate is exactly two files:

1. `docs/context_future/CTX_F5_3I_B2_TRUSTED_TOOL_RESPONSE_PROMOTION_MATERIAL.md`
2. `se/tests/architecture/test_ctx_f5_3i_b2_trusted_tool_response_promotion_material.py`

No `se/src/**` production file changes.

The architecture evidence must prove:
1. B1 currently returns SourcePromotionProof only;
2. B1 computes canonical result.output bytes + content digest internally;
3. MemoryPromotionIntent requires content_digest;
4. durable Memory admission accepts an explicit content value;
5. InMemoryToolResponsePayloadRepository is not durable authority;
6. exact same-read proof/content/digest binding is mandatory;
7. B1 public semantics must remain compatible;
8. runtime/reservation/admission/retrieval authority stays closed;
9. no production/runtime/schema/migration delta exists in B2 contract stage.

## 12. Exit gate

This contract/evidence candidate may reach FINAL GREEN only when:
- changed files remain exactly the two files above;
- exact-head Linux + Windows Architecture are GREEN/GREEN;
- no blocking P0/P1/P2 remains;
- current-main/dependency drift is refreshed;
- independent audit accepts or repairs the trusted material boundary.

Even after contract landing:

```text
B2 production PRE-CLAIM = SEPARATE / REQUIRED
B2 production CLAIM = NONE
production branch = NONE until independent PRE-CLAIM release
runtime/API/reservation/admission/retrieval authority = CLOSED
```

## Non-authority statement

CTX-F5-3I-B2 is a contract and architecture-evidence slice only.

It reads no live data, returns no production trusted material, issues no
reservation, admits no Memory, changes no source retention, opens no runtime
path, and grants no production merge authority.
