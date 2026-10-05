# CTX-F5-3I-B8 — Trusted Promotion Invocation Handoff

Status: **CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION**  
Canonical workspace: Issue #15  
Policy: Issue #85 v2.5  
Independent contract PRE-CLAIM: Issue #15 comment #5988129198

## 1. Frozen baseline and authority

```text
stage = CTX-F5-3I-B8
development baseline = a8b707ed4e05ddc1bac6bed98910d3ef4969feaf
baseline Architecture #2113 / 37262785593 = GREEN/GREEN
parent CTX-F5-3I-B7 = LANDED / CANONICAL / HEALTHY
class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = CLOSED
production CLAIM = NONE
merge authority = NONE
```

This stage freezes the authority and call shape for one future trusted-internal
TOOL_RESPONSE_PAYLOAD -> durable Memory promotion invocation handoff. It does
not implement that handoff and does not release a production caller.

Exact changed-file maximum for this stage:

1. `docs/context_future/CTX_F5_3I_B8_TRUSTED_PROMOTION_INVOCATION_HANDOFF_A8B707ED.md`
2. `se/tests/architecture/test_ctx_f5_3i_b8_trusted_promotion_invocation_handoff.py`

```text
docs delta = exact 1 NEW
architecture-evidence delta = exact 1 NEW
se/src/** delta = ZERO
cl/** delta = ZERO
production/runtime delta = ZERO
schema/migration delta = ZERO
ApplicationContainer/main wiring delta = ZERO
```

Any production/runtime/schema/client change invalidates this contract CLAIM.

## 2. Canonical parent surfaces

B7 canonically exposes the fail-closed typed resolver:

```python
StorageEngine.get_tool_response_payload_memory_promotion()
```

The B5 promotion surface remains exactly:

```python
promote(
    source_ref: ContextSourceRef,
    owner_user_id: str,
)
```

B8 does not widen either surface.

## 3. Explicit trusted invocation only

A future production handoff may run only when a separately authorized trusted
internal caller explicitly invokes it.

Promotion MUST NOT be implicitly triggered by:

- tool completion;
- capability completion;
- AgentToolResult COMMITTED observation;
- provider completion;
- retry, recovery, or resume;
- Session, Task, or Branch lifecycle;
- application startup or shutdown;
- event-bus publication;
- ContextBuilder, retrieval, Working Set, or ContextSnapshot assembly.

**Automatic promotion remains CLOSED.**

B8 creates no production caller, trigger, listener, background worker, hook, or
subscription.

## 4. Exact caller input remains B5-owned

A future handoff may receive only:

- one canonical `ContextSourceRef`;
- one authenticated/canonical `owner_user_id`.

It MUST NOT receive, derive as caller authority, or mint:

- content or content snapshot;
- content digest;
- `promotion_authority_id`;
- `SourcePromotionProof`;
- `PromotionReservation`;
- Memory id;
- reservation state;
- caller-selected repository, SQL session, UnitOfWork, or transaction state.

The caller MUST NOT treat `source_ref.owner_user_id` alone as authentication
authority. The supplied owner identity must originate from the separately
trusted/authenticated caller context. The landed B1/B5 source authority
independently re-proves that owner against durable source evidence.

## 5. B7 resolver is the only service acquisition seam

A future handoff MUST acquire the service through:

```python
storage.get_tool_response_payload_memory_promotion()
```

It MUST NOT:

- read `StorageEngine.services["tool_response_payload_memory_promotion"]`
  directly;
- construct `DurableToolResponsePayloadMemoryPromotion`;
- construct or replace B1/B3/B4/H-B2 dependencies;
- acquire SQLite sessions, engines, sessionmakers, repositories, or UoWs;
- create a parallel registry, service locator, or fallback promotion service.

Existing `ApplicationContainer.storage` is sufficient for a later trusted
application caller. B8 does not add an ApplicationContainer field and does not
change `main.py` wiring.

## 6. Resolve immediately before one invocation

A future handoff MUST resolve the current service immediately before the single
promotion call and MUST NOT cache or retain that returned service as
caller-owned authority across invocations.

The bounded future shape is:

```python
service = storage.get_tool_response_payload_memory_promotion()
result = await service.promote(
    source_ref=canonical_source_ref,
    owner_user_id=authenticated_owner,
)
return result
```

This is a contract shape only. No production implementation is released here.

The immediate-resolution rule preserves the landed B7 checks for:

- StorageEngine started state;
- active service generation;
- current SQLite availability;
- canonical registry entry;
- non-stale generation.

A caller-owned cached service reference would bypass the B7 resolver on later
invocations and is therefore prohibited.

## 7. Failure and cancellation authority remains lower-layer owned

A future handoff MUST NOT add:

- retry loops;
- exception normalization that changes B1/B3/B4/H-B2/B5 semantics;
- source repair;
- reservation repair;
- Memory repair;
- detached tasks;
- fire-and-forget promotion;
- hidden queueing or scheduling authority.

It must propagate lower-layer failure and cancellation semantics.

Scheduling placement, Agent critical-path placement, timeout ownership, and
budget accounting for a future real caller are outside this contract and
require the then-current owning authorities.

## 8. Public, model, routing, and UBQ authority remains closed

B8 grants no:

- HTTP/FastAPI route or dependency;
- client protocol;
- tool or capability registration;
- model-visible capability or schema;
- `capability_id`;
- invocation identity;
- quota admission;
- charge or refund event;
- timeout policy;
- DirectChat trigger;
- AgentRuntime trigger;
- Issue #156 routing or sandbox authority.

Any later public/model-callable or Agent critical-path caller requires a fresh
exact-main bilateral audit with Issue #156 and the then-current UBQ owner.

## 9. CAS and later CTX stages remain closed

TOOL_RESPONSE_PAYLOAD content remains opaque committed source material.

B8 does not dereference, hydrate, rewrite, strip, canonicalize, or otherwise
interpret asset-like values as CAS objects. It acquires no FileAsset,
FileBlob, ObjectStorage, provider-binding, lifecycle, deletion, or GC
authority.

B8 also does not release:

- Memory retrieval, search, or ranking;
- ContextBuilder injection;
- F6 Personalization;
- F7 pins, scoring, or dedupe;
- F9 Working Set or ContextSnapshot;
- F10 CompactContext;
- retention or destructive GC.

## 10. Dependency disposition at contract freeze

- AE-R12 #107 is COMPLETE / CLOSED / CANONICAL HEALTHY. Its landed recovery
  semantics remain boundary evidence only and transfer no B8 caller authority.
- UBQ-6 #148 is a separate TaskBudget-authority track. B8 creates no resource
  or quota identity. Any future Agent-path production caller must re-audit the
  then-current UBQ semantics.
- CAS-F7-T #74 retains CAS ownership. B8 does not reinterpret media or CAS
  identity.
- Issue #156 retains capability selection, routing, sandbox, and Agent-only
  cutover authority.
- Issue #31/R11 retains retention and destructive-GC authority.
- CL-UI integration is path- and authority-disjoint from this contract.

No dependency statement transfers implementation authority.

## 11. Production PRE-CLAIM remains closed

This contract release does not authorize any `se/src/**` edit or any
production invocation of `.promote(...)`.

A later production stage requires all of the following before implementation:

1. this B8 contract reaches independent FINAL;
2. the contract lands and is canonical/healthy on exact main;
3. Issue #15 is re-read against then-current source and dependencies;
4. a separate independent production PRE-CLAIM freezes the exact caller,
   exact production paths, authenticated owner source, scheduling/budget
   ownership, and cross-track fences;
5. production CLAIM is recorded separately.

Until then:

```text
production PRE-CLAIM = CLOSED
production CLAIM = NONE
F6 Personalization = CLOSED
automatic promotion = CLOSED
merge authority = NONE
```
