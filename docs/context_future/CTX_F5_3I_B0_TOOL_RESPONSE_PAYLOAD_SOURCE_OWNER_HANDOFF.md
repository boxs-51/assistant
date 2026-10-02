# CTX-F5-3I-B0 — TOOL_RESPONSE_PAYLOAD Source-Owner Handoff Contract

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.

## Status

```text
stage = CTX-F5-3I-B0
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
exact development baseline = a5d343ea33607f66d50347720b7b18b82b31090a
baseline Architecture #1768 = GREEN/GREEN
parent CTX-F5-3I-A = LANDED / CANONICAL / HEALTHY
selected source kind for handoff contract = TOOL_RESPONSE_PAYLOAD
production source adapter = CLOSED
runtime/API/retrieval/ContextBuilder/model visibility = CLOSED
production PRE-CLAIM = NONE
B1 production PRE-CLAIM = CLOSED
production/runtime/schema/migration delta = ZERO
merge authority = NONE
```

B0 selects `ContextSourceKind.TOOL_RESPONSE_PAYLOAD` only for a source-owner
handoff contract. It does not make that source production-supported and does not
implement `SourcePromotionAuthorityPort`.

This file incorporates the accepted V1 lineage/identity freeze, the V2
successful-only and opaque-JSON narrowing, and the V3 successful-result
`retryable=False` integrity fence.

## 1. Durable source authority starts from Agent evidence, not caller TRP

A caller/in-memory `ToolResponsePayload` is never durable liveness,
authorization, owner, or content authority.

A future trusted re-proof must start from one exact validated
`ContextSourceRef` and require:

```text
source_ref.source_kind == TOOL_RESPONSE_PAYLOAD
source_ref.owner_user_id == requested owner_user_id
```

`source_ref.metadata["source_result_id"]` is an untrusted lookup hint only.
It is not source authority because metadata is not part of the canonical
`ContextSourceRef` identity tuple.

The lookup hint may locate an `AgentToolResultRecord`, but trusted authority is
minted only after the complete durable lineage, terminal-result semantics,
owner proof, content digest, and reconstructed payload identity all succeed.

## 2. Exact successful-result eligibility — V2 + V3

The first B0 source class is successful committed tool results only.

Before proof construction, the durable result must satisfy exactly:

```text
result.commit_state == COMMITTED
result.success is True
result.error_code is None
result.error_message is None
result.retryable is False
```

The canonical Agent/R7 successful terminal projection sets
`retryable=False`. `AgentToolResultRecord.retryable` is persisted as a
distinct field, while the current SQL model does not add a database constraint
that couples `success=True` to `retryable=False`.

Therefore a durable row such as:

```text
COMMITTED
+ success=True
+ retryable=True
```

is **MALFORMED / NON-CANONICAL / INELIGIBLE** for CTX source re-proof.

Required fail-closed behavior:

- mint no `SourcePromotionProof`;
- issue no new promotion reservation;
- perform no repair or normalization write;
- do not reinterpret the row from mutable `CapabilityInvocation` state;
- do not infer success authority from output presence alone.

Failed COMMITTED results remain CLOSED. A later source contract must freeze one
exact canonical failure projection before they may become promotable.

## 3. Exact durable lineage and canonical owner proof

After loading the result by the untrusted source-result hint, a future trusted
source adapter must load and match the exact durable lineage:

```text
AgentToolResultRecord(result.id)
-> AgentExecutionRecord(result.execution_id)
-> canonical chat_data.Session(execution.session_id)
-> CapabilityInvocationRecord(result.invocation_id)
-> AgentToolCallRecord(execution_id, result.tool_call_id)
```

Required owner/identity checks include:

```text
Session.user_id is present and non-empty
Session.user_id == requested owner_user_id == source_ref.owner_user_id

invocation.execution_id == result.execution_id
invocation.session_id == execution.session_id
invocation.owner_user_id == Session.user_id
invocation.tool_call_id == result.tool_call_id
invocation.capability_id == result.capability_id

tool_call.execution_id == result.execution_id
tool_call.iteration_id == result.iteration_id
tool_call.tool_call_id == result.tool_call_id
tool_call.invocation_id == result.invocation_id
tool_call.capability_id == result.capability_id
```

An `AgentSessionRecord.owner_user_id` is not a substitute for canonical Chat
Session ownership in this CTX source chain.

Mutable AgentExecution lease/state/revision and mutable
CapabilityInvocation revision/state/attempt/outcome are not immutable source
authority.

## 4. Authoritative content and exact payload reconstruction

For an eligible successful result, the authoritative source content is exactly
the durable `AgentToolResultRecord.output`.

The future trusted adapter must canonicalize that exact JSON using existing
CTX-F1 `canonical_payload_bytes(...)` rules and derive:

```text
content_digest = sha256(canonical_payload_bytes(result.output))

payload_id = tool_response_payload_id(
    source_result_id=result.id,
    invocation_id=result.invocation_id,
    execution_id=result.execution_id,
    tool_call_id=result.tool_call_id,
    logical_capability_id=result.capability_id,
    content_digest=content_digest,
    payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
)
```

It must then require:

```text
derived payload_id == source_ref.authority_id
metadata source_result_id hint == result.id
```

The metadata hint is compared only after server-side reconstruction succeeds.

Caller-supplied `payload.content`, `content_digest`, metadata, owner,
session, `source_commit_state`, or process-local repository membership is
never durable content/read authority.

The first B0 contract authorizes whole canonical JSON payload only. Derived,
subset, transformed, summarized, or selectively extracted promotion remains
CLOSED.

## 5. authority_state_token semantic freeze

The future source-owner `authority_state_token` must bind immutable successful
source semantics. Its semantic input includes at minimum:

```text
domain = ctx-trp-source-state-v1
result.id
result.execution_id
result.iteration_id
result.invocation_id
result.tool_call_id
result.capability_id
commit_state = COMMITTED
success = true
error_code = null
error_message = null
retryable = false
payload_schema_version
canonical content_digest(result.output)
canonical reconstructed payload_id
owner_user_id
session_id
```

Omission of `retryable=false` is not permitted.

The token may be a deterministic canonical hash over this material. Exact
encoding/helper implementation remains future production work.

The token must not bind mutable runtime state as freshness authority, including:

- AgentExecution lease/state/revision;
- CapabilityInvocation revision/state/attempt/outcome;
- target/implementation/connection identity;
- wall-clock freshness.

Those remain owned by R12/R6/R7 or routing/runtime authorities.

## 6. Deterministic proof receipt and replay semantics

`proof_receipt_id` must be deterministic and stable for one exact canonical
source proof, under a distinct domain such as
`ctx-trp-proof-receipt-v1`.

It binds at minimum:

```text
source_ref.context_source_id
source_ref.authority_id / payload_id
source_result_id
owner_user_id
MEMORY_PROMOTION scope
```

Retrying re-proof of the same live immutable source yields the same
`proof_receipt_id` / `authority_state_token` tuple.

The source owner must not mint a fresh receipt merely to bypass F5-3D/F
cross-intent conflict semantics.

Consequently:

- one immutable TOOL_RESPONSE_PAYLOAD proof tuple may authorize at most one
  exact canonical `MemoryPromotionIntent`;
- exact same-intent retry may converge/idempotently reuse authority;
- a different intent attempting to reuse that proof tuple fails closed.

## 7. Retention / GC boundary

R11/R12/Agent remain source-liveness and retention owners.

Before trusted re-proof, any missing or inconsistent source evidence fails
closed:

```text
result missing / GC-collected
OR result not exact eligible successful COMMITTED state
OR execution missing
OR canonical Chat Session missing/ownerless/foreign
OR invocation missing/inconsistent
OR tool call missing/inconsistent
OR reconstructed payload_id mismatch
=> no SourcePromotionProof
=> no new durable reservation
```

A CTX proof or reservation does not become an Agent live root.

Once a durable promotion reservation has already been successfully issued from
a trusted proof:

- later normal Agent/R11 GC does not retroactively mutate that durable
  reservation;
- the reservation does not pin Agent history;
- later source disappearance cannot mint a replacement proof;
- reservation consume/revoke semantics remain CTX F5 reservation authority.

## 8. Opaque JSON / CAS non-dereference boundary — V2

B0 owns exact canonical JSON bytes only. Embedded asset/file/provider-looking
values are opaque JSON and do not become CAS authority.

Examples include an embedded:

- `asset_id`;
- `asset://...` URI;
- provider URI or provider file ID;
- base64-looking value;
- object key;
- filesystem path.

B0 must not:

- validate, hydrate, dereference, open, repair, strip, or rewrite such values as
  CAS objects;
- acquire FileAsset/FileBlob/ObjectStorage/provider lifecycle authority;
- create a derived ASSET source proof;
- use Memory retrieval as a CAS bypass.

CAS validation/hydration/dereference/lifecycle authority = NONE.

`FUTURE-FENCE-CAS-CTX-OPAQUE-JSON-PROJECTION-1` remains mandatory: if a later
CTX stage opens retrieval, ContextBuilder, or model visibility, it must either
preserve an opaque/text serialization boundary or obtain an explicit CAS
handoff plus owner/readability re-proof before structural asset projection.

That future fence is non-blocking for B0 because retrieval/model visibility
remain CLOSED.

## 9. External authority boundaries

B0 must not:

- re-admit or recharge UBQ quota;
- mint a new invocation_id or capability_id;
- reinterpret R6/R7 remote outcome or side-effect authority;
- modify a COMMITTED AgentToolResult;
- change R11/R12 retention, GC, lease, recovery, or continuation semantics;
- create #156 capability target/routing/fingerprint/sandbox authority;
- implement runtime/container promotion wiring;
- expose HTTP/public/model-callable promotion;
- open Memory retrieval/search/ranking/vector/index/chunk/embedding;
- inject Memory into ContextBuilder or model Working Set.

CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE.

## 10. Exact B0 candidate scope

CTX-F5-3I-B0 is exactly two files:

- `docs/context_future/CTX_F5_3I_B0_TOOL_RESPONSE_PAYLOAD_SOURCE_OWNER_HANDOFF.md`
- `se/tests/architecture/test_ctx_f5_3i_b0_tool_response_payload_source_owner_handoff.py`

No `se/src/**` production file changes.

Architecture evidence must encode V1 + V2 + V3 fences and prove current
canonical code facts needed by the handoff, including:

1. existing CTX-F1 canonical JSON/content-digest/payload-id construction;
2. existing TOOL_RESPONSE_PAYLOAD projection binds canonical payload identity;
3. durable AgentToolResult persists success/error/retryable/commit-state fields;
4. canonical Agent/R7 successful terminal projection sets `retryable=False`;
5. the SQL result model has no success/retryable coupling constraint;
6. exact successful-result eligibility and malformed-row fail-closed rule;
7. exact Session/Execution/Invocation/ToolCall lineage and owner checks;
8. deterministic state-token and proof-receipt semantics;
9. pre-proof missing/GC fail-closed and post-reservation non-pinning behavior;
10. opaque JSON / CAS non-dereference fence;
11. production/runtime/API/retrieval/model-visibility authority remains CLOSED;
12. zero production/runtime/schema/migration delta.

## 11. Exit gate and next-stage fence

B0 may reach independent FINAL GREEN only when:

- changed files remain exactly the two files above;
- no `se/src/**` production delta exists;
- exact-head Linux + Windows Architecture are GREEN/GREEN;
- no blocking P0/P1/P2 remains;
- exact main/base/head drift is revalidated under Issue #15.

Even after B0 FINAL GREEN and landing:

```text
B0 production source-adapter PRE-CLAIM = NONE
B1 production PRE-CLAIM = CLOSED
runtime/API/retrieval/ContextBuilder/model visibility = CLOSED
```

A separately frozen B1 production PRE-CLAIM and independent release are required
before any production source adapter, wiring, runtime, or public/model-facing
promotion implementation.

## Non-authority statement

CTX-F5-3I-B0 is a source-owner handoff contract and architecture-evidence slice
only.

It implements no source adapter, mints no live production proof, issues no
reservation, changes no runtime behavior, creates no Memory, performs no CAS
dereference, changes no Agent retention, and grants no production merge
authority.
