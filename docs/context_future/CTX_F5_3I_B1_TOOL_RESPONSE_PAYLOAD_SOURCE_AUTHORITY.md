# CTX-F5-3I-B1 — Trusted TOOL_RESPONSE_PAYLOAD Source Authority

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.  
Claim comment: Issue #15 comment `5947732731`.

## Status

```text
stage = CTX-F5-3I-B1
class = PRODUCTION / TRUSTED SOURCE AUTHORITY SERVICE ONLY
claim base = 16e93e72d005f952c0f405522b37ef3e6fba7522
parent CTX-F5-3I-B0 = LANDED / CANONICAL / HEALTHY
independent PRE-CLAIM = PASS / RELEASED
selected source kind = TOOL_RESPONSE_PAYLOAD
runtime/container/API wiring = CLOSED
reservation/Memory admission = CLOSED
retrieval/ContextBuilder/model visibility = CLOSED
repository/model/schema/migration delta = ZERO
merge authority = NONE
```

B1 implements exactly one production source-owner adapter:

`DurableToolResponsePayloadSourceAuthority.reprove_for_memory_promotion(...)`

It implements the existing `SourcePromotionAuthorityPort` only for
`ContextSourceKind.TOOL_RESPONSE_PAYLOAD`.

## 1. Exact production scope

Production scope is exactly:

- `se/src/infrastructure/storage/services/tool_response_payload_source_authority.py`

Dedicated evidence is exactly:

- `docs/context_future/CTX_F5_3I_B1_TOOL_RESPONSE_PAYLOAD_SOURCE_AUTHORITY.md`
- `se/tests/unit/test_ctx_f5_3i_b1_tool_response_payload_source_authority.py`
- `se/tests/integration/test_ctx_f5_3i_b1_tool_response_payload_source_authority.py`
- `se/tests/architecture/test_ctx_f5_3i_b1_tool_response_payload_source_authority.py`

The complete candidate is therefore exactly five files.

No Agent repository, CapabilityInvocation repository, Session repository,
SQL model, schema, migration, UnitOfWork, DatabaseDriver, runtime, container,
handler, API, source adapter, or Memory-promotion primitive is modified.

Schema delta: ZERO.  
Migration delta: ZERO.  
Runtime/container/API wiring delta: ZERO.

## 2. Caller source_ref is lookup/claim material only

B1 first validates the supplied `ContextSourceRef`, but it never treats the
whole caller object as trusted provenance.

Required caller gate:

```text
source_kind == TOOL_RESPONSE_PAYLOAD
authority_version is None
owner_user_id == requested owner_user_id
session_id is a canonical non-empty string
task_id is None
branch_id is None
source_state == COMMITTED
metadata["source_result_id"] is a canonical non-empty string
```

Additional caller metadata and caller `source_created_at` are explicitly
non-authoritative.

The input `source_ref` is never embedded unchanged in the returned
`SourcePromotionProof`.

## 3. One fresh read-only session and exact durable lineage

Each re-proof call opens exactly one injected `AsyncSession` context.

B1 performs read-only durable verification:

```text
AgentToolResultRecord(source_result_id)
-> AgentExecutionRecord(result.execution_id)
-> canonical chat_data.Session(execution.session_id)
-> CapabilityInvocationRecord(result.invocation_id)
-> AgentToolCallRecord(execution_id, result.tool_call_id)
```

The service performs SELECT/get reads only.

It must not call:

- `commit()`;
- `flush()`;
- `rollback()`;
- `add()`;
- `delete()`;
- SQL UPDATE/INSERT/DELETE;
- `with_for_update()`;
- repository mutation or repair APIs.

No row lock, repair, normalization write, lifecycle mutation, lease mutation,
or recovery mutation is authorized.

## 4. Exact successful-source eligibility

A durable tool result is eligible only when all conditions are exact:

```text
result.commit_state == COMMITTED
result.success is True
result.error_code is None
result.error_message is None
result.retryable is False
```

Any missing row, malformed row, failed result, retryable successful row, or
lineage mismatch fails closed and produces no proof.

B1 does not infer success from output presence and does not reinterpret
CapabilityInvocation runtime state.

## 5. Canonical owner and lineage authority

Canonical ownership comes only from `chat_data.Session.user_id`.

Required equality includes:

```text
Session.id == AgentExecutionRecord.session_id == source_ref.session_id
Session.user_id == requested owner_user_id == source_ref.owner_user_id

invocation.invocation_id == result.invocation_id
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

Mutable Agent execution state/revision/lease and mutable invocation
state/revision/attempt/outcome are not proof authority.

## 6. Exact content and payload reconstruction

Authoritative content is exactly `AgentToolResultRecord.output`.

B1 uses existing CTX-F1 canonical JSON semantics:

```text
canonical_content = canonical_payload_bytes(result.output)
content_digest = sha256(canonical_content)

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

The server-reconstructed `payload_id` must equal the caller's claimed
`source_ref.authority_id`.

Embedded asset/provider-looking JSON remains opaque bytes for CTX identity.
B1 performs no CAS dereference, hydration, validation, repair, or lifecycle
operation.

## 7. Mandatory V2 reconstructed trusted snapshot

The independent PRE-CLAIM P1 repair is mandatory.

After durable re-proof B1 constructs a new canonical `ContextSourceRef`
using only re-proved durable semantics:

```text
source_kind       = TOOL_RESPONSE_PAYLOAD
authority_id      = server-reconstructed payload_id
authority_version = None
owner_user_id     = canonical chat Session.user_id
session_id        = AgentExecutionRecord.session_id
task_id           = None
branch_id         = None
source_created_at = None
source_state      = COMMITTED
metadata          = exactly {"source_result_id": result.id}
```

Then B1:

1. revalidates reconstructed ContextSourceRef integrity;
2. requires reconstructed `context_source_id` to equal the caller's claimed
   canonical source identity;
3. requires reconstructed payload identity to equal caller authority_id;
4. returns only the reconstructed ref inside `SourcePromotionProof`.

Caller extra metadata is discarded.  
Caller `source_created_at` is discarded.  
Caller provenance cannot vary the trusted proof snapshot.

## 8. Deterministic authority_state_token

The state token uses domain:

`ctx-trp-source-state-v1`

Its canonical hash material binds:

- result id;
- execution id;
- iteration id;
- invocation id;
- tool-call id;
- capability id;
- `COMMITTED`;
- `success=true`;
- null error code;
- null error message;
- `retryable=false`;
- payload schema version;
- canonical content digest;
- reconstructed payload id;
- canonical owner user id;
- canonical session id.

It excludes mutable execution lease/state/revision and mutable invocation
runtime state.

Same immutable durable source semantics produce the same state token.

## 9. Deterministic proof_receipt_id

The receipt uses a separate domain:

`ctx-trp-proof-receipt-v1`

It binds at least:

- reconstructed `context_source_id`;
- reconstructed authority/payload id;
- durable source result id;
- canonical owner user id;
- `MEMORY_PROMOTION` scope;
- exact authority state token.

Retrying re-proof for the same immutable live source produces the same
`proof_receipt_id` / `authority_state_token` tuple.

The service does not mint a fresh receipt to bypass downstream exact-intent
conflict semantics.

## 10. Provenance-determinism evidence

Unit and integration evidence must prove:

1. caller extra metadata keys are discarded;
2. caller `source_created_at` is discarded;
3. two caller refs with the same canonical source identity but different
   non-authority timestamp/metadata produce the exact same reconstructed
   proof snapshot;
4. they produce the same receipt and state token;
5. reconstructed metadata is exactly
   `{"source_result_id": result.id}`;
6. downstream `MemoryPromotionIntent` bytes/JSON cannot vary solely because
   caller provenance varied;
7. the input source_ref object is not reused as the trusted snapshot.

## 11. Error and cancellation behavior

Durable missing/inconsistent/ineligible evidence fails closed through the
service-local rejection surface.

SQLAlchemy read/storage failures map to the service-local unavailable surface.

`asyncio.CancelledError` propagates unchanged. B1 has no retry loop,
sleep loop, fallback source, repair path, or alternate authority source.

## 12. External authority fences

B1 does not acquire authority to:

- re-admit or recharge UBQ quota;
- mint or redefine invocation_id/capability_id;
- reinterpret R6/R7 remote outcome or side-effect authority;
- change R11/R12 retention, GC, recovery, lease, or continuation semantics;
- dereference or mutate CAS assets/providers;
- implement #156 capability routing/fingerprint/sandbox semantics;
- issue or consume a Memory promotion reservation;
- write a durable Memory record;
- expose HTTP/public/model-callable promotion;
- open Memory retrieval/search/ranking/vector/index/chunk/embedding;
- inject Memory into ContextBuilder or model Working Set.

CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE.

## 13. Drift guard

The PRE-CLAIM release was audited against:

```text
canonical main = 16e93e72d005f952c0f405522b37ef3e6fba7522
UBQ-4 PR #190 HEAD = 37cd28ac6ef5a17b19a5042a39049741e3209979
R12-F2 PR #196 HEAD = f742833405b67ad27ca9a18882ccf958d69377d4
```

Before every material transition, exact current main and those active
dependency heads must be refreshed under Issue #15.

Any movement affecting persisted source identity/ownership semantics, or any
need for a sixth file/out-of-scope production change, returns B1 to HOLD and
requires re-audit.

## 14. Exit gate

B1 may become READY only after:

- exact five-file scope remains intact;
- implementation satisfies this frozen contract;
- dedicated unit/integration/architecture evidence passes;
- full required Linux + Windows Architecture CI is GREEN/GREEN;
- no blocking P0/P1/P2 remains;
- exact HEAD/base/main/dependency drift is independently revalidated;
- independent implementation FINAL GREEN is issued.

Production merge is not authorized by CLAIM, implementation completion,
CI GREEN, or FINAL GREEN. A separate explicit user merge authorization remains
required.
