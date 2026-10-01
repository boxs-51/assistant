# CTX-F5-3I-A — Promotion Source Eligibility & Authority Handoff Freeze

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.

## Status

```text
stage = CTX-F5-3I-A
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
development baseline = 1fc22e2624aaedddb11885c780c1b45b7602321e
baseline Architecture #1710 attempt 2 = GREEN/GREEN
parent CTX-F5-3H-B2 = LANDED / CANONICAL / HEALTHY
parent H-B3 = 30-of-30 SATISFIED
production delta = ZERO
runtime delta = ZERO
schema/migration delta = ZERO
concrete supported promotion source kinds = NONE
production PRE-CLAIM = NONE
production CLAIM = NONE
```

CTX-F5-3I-A freezes how an existing `ContextSourceKind` can become eligible for
trusted Memory promotion after H-B2 made durable SQLite admission canonical.

I-A activates no source kind, implements no source adapter, exposes no runtime or
public promotion path, and transfers no authority from the source owner to CTX.

## 1. Canonical source vocabulary and identity facts

The current canonical source vocabulary is exactly:

```text
SESSION
TASK
BRANCH
AGENT_TRANSCRIPT
ASSET
TOOL_RESPONSE_PAYLOAD
```

There is no `ContextSourceKind.MEMORY`.

Current versioning authority is also frozen:

```text
versioned source identities:
  TASK
  BRANCH
  AGENT_TRANSCRIPT

unversioned source identities:
  SESSION
  ASSET
  TOOL_RESPONSE_PAYLOAD
```

A `ContextSourceRef` is a CTX projection identity. It is not by itself a live
source-owner freshness, liveness, retention, authorization, or deletion proof.

The already landed `SourcePromotionAuthorityPort.reprove_for_memory_promotion(...)`
is a dormant contract. I-A does not implement that port.

## 2. SESSION — HOLD / not eligible for activation in I-A

`SESSION` identity is unversioned.

Current projection binds the canonical Session ID and owner but has no monotonic
Session revision in the CTX identity.

Therefore Session promotion cannot be activated merely from:

- Session ID;
- status;
- created timestamp;
- a previously projected `ContextSourceRef`.

Before a later stage can activate SESSION, the Session owner must define a
deterministic current-state proof that can reject stale or foreign promotion
material and must freeze retention/deletion behavior for that proof.

I-A does not define such a proof.

## 3. TASK — candidate only / not released

`TASK` identity is versioned by `task.revision`.

That makes TASK a possible future source candidate, but it does not transfer
Agent Task authority to CTX.

A later TASK-specific source stage must re-prove at least:

```text
exact task id
exact current task revision
exact owning session
exact owner user
exact current task state required by that source contract
retention / deletion / liveness authority
```

The live Task row and its revision/state remain owned by Agent persistence and
the landed R11/R12 authority chain where applicable.

A stale F3/F4 digest is never sufficient promotion authorization.

## 4. BRANCH — candidate only / not released

`BRANCH` identity is versioned by `branch.revision`.

A future BRANCH-specific stage must re-prove at least:

```text
exact branch id
exact current branch revision
exact parent task lineage
exact owner user via canonical task/session ownership
exact branch resolution state required by that source contract
any current-execution binding required by the source owner
retention / deletion / liveness authority
```

CTX must not acquire branch resolution, fork/adopt/discard, continuation, or
recovery authority merely to make a Branch promotable.

## 5. AGENT_TRANSCRIPT — candidate only / not released

`AGENT_TRANSCRIPT` identity is versioned by the canonical transcript reference
and transcript version.

A later transcript-specific source stage must obtain an explicit handoff from
the checkpoint/transcript owner and re-prove exact transcript version, owner and
liveness.

R11's landed checkpoint/transcript persistence and retention/GC boundaries
remain canonical evidence. Active R12 recovery/continuation authority is not
transferred to CTX.

CTX must not manufacture transcript freshness or extend transcript lifetime by
creating a Memory promotion proof.

## 6. ASSET — candidate only / not released

`ASSET` CTX identity is intentionally stable across FileAsset revision.
FileAsset revision is projected metadata and is not part of CTX ASSET identity.

Therefore ASSET promotion requires an explicit CAS-owned re-proof boundary.

A later ASSET-specific source stage must obtain from CAS at least:

```text
exact asset authority
authenticated owner
READY/current admissible lifecycle state
current revision/state evidence owned by CAS
authoritative content read/hydration boundary
retention/deletion/provider lifecycle semantics
```

No provider URI, filesystem path, object key, provider handle, or projected
metadata value becomes promotion authority.

CAS Issue #74 retains FileAsset/FileBlob/ObjectStorage/provider/lifecycle/GC
authority.

## 7. TOOL_RESPONSE_PAYLOAD — candidate only / not released

`TOOL_RESPONSE_PAYLOAD` identity is unversioned.

Current projection already requires durable COMMITTED tool-result lineage, but
that projection is not by itself a renewable promotion authorization.

A later tool-payload-specific source stage must re-prove at least:

```text
exact payload id
exact durable result id
exact invocation id / execution lineage
exact logical capability id
authenticated owner
COMMITTED result authority
content immutability / digest binding
retention / deletion / liveness owner
```

Such a stage must not redefine:

- CapabilityRuntime execution semantics;
- UBQ logical tool-call quota identity;
- R6/R7 outcome/reconciliation authority;
- Agent-only target/routing/fingerprint authority.

## 8. Source-owner handoff required before any I-B production PRE-CLAIM

Exactly one source kind must be selected by a later stage.

Before that source can receive a production PRE-CLAIM, its owner must freeze:

1. canonical authority ID and current-state lookup;
2. authenticated owner-user verification;
3. revision/state/receipt semantics sufficient to reject stale or foreign proof;
4. authoritative content-read boundary;
5. retention/deletion/liveness owner;
6. exact derivation and verification of `proof_receipt_id`;
7. exact derivation and verification of `authority_state_token`;
8. source mutation behavior after proof issuance;
9. whether one proof is bound to one exact content digest;
10. missing/deleted/revoked/unauthorized failure behavior;
11. retry/replay behavior;
12. no cross-store atomicity claim beyond guarantees owned by that source.

The source owner, not CTX, defines what `authority_state_token` proves.

A timestamp alone is not deterministic freshness authority.

## 9. SourcePromotionAuthorityPort remains contract-only

The existing contract remains:

```python
class SourcePromotionAuthorityPort(Protocol):
    async def reprove_for_memory_promotion(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> SourcePromotionProof: ...
```

I-A does not:

- add an implementation;
- register an implementation in a runtime/container;
- choose a source kind;
- call this port from an API or model-facing path;
- grant a source adapter persistence or lifecycle ownership.

`SourcePromotionProof.proof_receipt_id` and
`SourcePromotionProof.authority_state_token` remain opaque CTX values whose
meaning must be guaranteed by the owning source contract.

## 10. Cross-track authority boundaries

### CAS / Issue #74

Material only for an ASSET-backed source or any FileAsset/FileBlob/ObjectStorage,
provider hydration, lifecycle or GC behavior.

No CAS authority is transferred by I-A.

### R11 / closed Issue #31 and AE-R12 / Issue #107

R11's landed checkpoint/transcript retention and destructive-GC matrix remains
canonical. R12 is the active successor for recovery/continuation semantics.

TASK, BRANCH or AGENT_TRANSCRIPT activation requires a fresh overlap audit before
production PRE-CLAIM.

No R11/R12 authority is transferred by I-A.

### UBQ-3 / Issue #145

UBQ owns renewable logical tool-call quota semantics.

A future model-callable promotion capability or TOOL_RESPONSE_PAYLOAD execution
path must preserve the canonical `invocation_id` / `capability_id` accounting
boundary and must not create an extra logical charge by changing target,
implementation or connection.

I-A acquires no UBQ authority.

### Agent-only / Issue #156

Future capability selection, target-aware routing, sandboxing and Agent-only
cutover remain external.

I-A creates no capability, target, route, fingerprint or sandbox behavior.

## 11. I-B entry gate

I-A does not predetermine the first supported source.

A later CTX-F5-3I-B may be defined only when all of the following are true:

1. I-A is canonical and healthy;
2. one exact source kind is selected;
3. that source owner has an explicit authority handoff contract;
4. the current canonical main is healthy;
5. fresh CAS/R11/R12/UBQ/#156 overlap audit is complete as applicable;
6. exact production paths and non-scope are frozen;
7. independent PRE-CLAIM release is recorded.

No I-B production branch may be created by implication from I-A.

## 12. Authority remaining CLOSED

I-A does not release:

- any `se/src/**` production implementation;
- source-specific loader or re-proof adapter;
- any concrete supported promotion source kind;
- runtime/container promotion wiring;
- HTTP/public/model-callable promotion;
- `ContextSourceKind.MEMORY`;
- Memory retrieval/search/ranking/vector/index/chunk/embedding authority;
- ContextBuilder or model Working Set Memory visibility;
- revocation/erasure/tombstone implementation;
- non-SQLite admission authority;
- CAS/R11/R12/UBQ/#156 authority transfer;
- F6 Personalization;
- F7 Pins/scoring/dedupe;
- F9 Working Set / ContextSnapshot;
- F10 CompactContext.

## 13. Exact scope and acceptance

CTX-F5-3I-A is exactly two files:

- `docs/context_future/CTX_F5_3I_A_PROMOTION_SOURCE_ELIGIBILITY_AUTHORITY_HANDOFF.md`
- `se/tests/architecture/test_ctx_f5_3i_a_promotion_source_eligibility_authority_handoff.py`

No `se/src/**` file changes.

Architecture evidence must freeze at least:

1. exact six current `ContextSourceKind` values;
2. no `ContextSourceKind.MEMORY`;
3. exact versioned set = TASK / BRANCH / AGENT_TRANSCRIPT;
4. exact unversioned set = SESSION / ASSET / TOOL_RESPONSE_PAYLOAD;
5. existing projection functions remain present for all six kinds;
6. `SourcePromotionAuthorityPort.reprove_for_memory_promotion(...)` exists;
7. no source kind is supported/promotable by I-A;
8. source-owner handoff precedes any I-B production PRE-CLAIM;
9. all runtime/API/retrieval/ContextBuilder authority remains closed;
10. zero production/runtime/schema/migration delta.

## Non-authority statement

CTX-F5-3I-A is a source-eligibility and authority-handoff contract only.

It proves no source is currently promotable, implements no source re-proof,
creates no Memory, consumes no reservation, exposes no API, changes no runtime,
and grants no production merge authority.
