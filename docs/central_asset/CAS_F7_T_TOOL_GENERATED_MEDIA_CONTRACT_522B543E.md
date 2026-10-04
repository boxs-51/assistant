# CAS-F7-T — Tool-Generated Media Canonicalization Contract Freeze

Status: **CONTRACT / PRE-CLAIM PREPARATION CANDIDATE / ZERO-PRODUCTION**

Primary workspace: Issue #74  
Canonical governance: Issue #85 v2.5  
Independent preparation release: Issue #74 comment #5979015990  
Frozen baseline: `main@522b543e66f309085729e57a2248258b7c4f2599`

```text
stage = CAS-F7-T
production/runtime/schema/migration delta = ZERO
production PRE-CLAIM PASS = NOT YET
production CLAIM = NONE
merge authority = NONE
F8 = CLOSED
#166 CAS-B1 = RESERVED / HARD HOLD
READY deletion / lifecycle / GC / provider cleanup = CLOSED
```

## 1. Purpose and authority boundary

F7-T freezes the future CAS boundary for canonicalizing tool-generated media only after Agent has established an immutable durable successful tool result. This document creates no production authority.

F7-P1 and F7-S remain LANDED / CANONICAL / HEALTHY. F7-T does not reopen provider-response canonicalization, provider fallback, Agent execution ownership, UBQ accounting, CTX promotion, sandbox routing, lifecycle, deletion, GC, or provider cleanup.

The future F7-T path is a **read-only derivation from committed Agent result authority**. It MUST NOT rewrite, normalize, replace, or otherwise mutate `AgentToolResultRecord.output`.

## 2. Exact source eligibility

A tool result is eligible for F7-T admission only when the same durable `AgentToolResultRecord` satisfies all of:

```text
result.commit_state == "COMMITTED"
result.success is True
result.error_code is None
result.error_message is None
result.retryable is False
```

`PROVISIONAL`, failed, retryable, incomplete, cancelled, ambiguous, or OUTCOME_UNKNOWN execution state is not F7-T ingest authority.

F7-T does not infer success from transport completion, tool events, caller metadata, provider output, sandbox state, or a media-looking JSON value. Agent/R6/R7/R12 remain the owners of remote/tool outcome truth and durable commitment.

## 3. Trustworthy media-envelope admission

Arbitrary JSON strings, URLs, paths, base64-looking values, provider IDs, sandbox paths, filenames, MIME-looking fields, or nested objects are insufficient.

Future production admission requires a **tool-owned typed media envelope** whose source class is explicitly declared by the tool/capability contract and whose bytes are available through an already-authorized bounded source. The envelope must bind at minimum:

```text
source_result_id
execution_id
iteration_id
invocation_id
tool_call_id
capability_id
media_ordinal
declared_media_kind
declared_mime_type
declared_filename
bounded source descriptor
```

The envelope is evidence for locating media; it is not canonical identity and cannot override owner, result lineage, content bounds, or Agent commitment authority.

Generic provider URL, arbitrary HTTP URL, local path, sandbox path, object key, signed URL, browser URL, provider file ID, and remote handle source classes remain CLOSED unless a separately released source-class contract proves retrieval authority.

## 4. Durable lineage and owner proof

Canonical owner authority must be re-proved from durable Agent lineage:

```text
AgentToolResultRecord(result.id)
-> AgentExecutionRecord(result.execution_id)
-> canonical chat_data.Session(execution.session_id)
-> Session.user_id
```

The result lineage must also agree with the durable invocation/tool-call authority:

```text
result.invocation_id == canonical CapabilityInvocationRecord.id
result.tool_call_id == canonical AgentToolCallRecord.tool_call_id
result.execution_id == canonical execution/tool-call execution_id
result.capability_id == canonical invocation/tool-call capability identity
```

Caller payload owner fields, tool output owner fields, provider metadata, envelope metadata, and sandbox metadata have **no owner authority**.

## 5. Immutable Agent result and CTX coexistence

The authoritative CTX TOOL_RESPONSE_PAYLOAD source content remains exactly the durable committed `AgentToolResultRecord.output`.

F7-T MUST NOT:
- rewrite `AgentToolResultRecord.output` to replace a media envelope with `asset://...`;
- mutate CTX ToolResponsePayload source bytes or source identity;
- dereference embedded asset/provider-looking JSON on behalf of CTX;
- acquire Memory promotion, retrieval, ranking, search, embedding, or ContextBuilder authority.

The future canonical asset is a separate CAS-derived publication keyed back to the immutable committed result.

## 6. Post-COMMITTED trigger and publication surface

The only admissible trigger is a **post-COMMITTED read-only derivation** after the successful result is durably visible.

The future publication model is frozen as a separate durable F7-T projection/association, not a mutation of the source result. The association must be addressable by a deterministic source key and must bind:

```text
source_result_id
execution_id
invocation_id
tool_call_id
capability_id
media_ordinal
asset_id
owner_user_id
origin_type = TOOL
```

The exact production storage representation for that association is **not authorized by this contract candidate** and requires the independent production PRE-CLAIM audit. Schema/migration authority remains NONE.

Consumers may use the separate association to resolve canonical `asset://<asset_id>` identity while preserving the original committed tool-result JSON byte/semantic authority.

## 7. Deterministic idempotency key and duplicate suppression

Current `AssetService.ingest_stream()` allocates new asset/blob IDs and is not itself idempotent. Therefore F7-T MUST establish duplicate suppression **before** any production PRE-CLAIM can PASS.

The future logical canonicalization key is frozen conceptually as:

```text
F7TSourceKey =
(
  source_result_id,
  invocation_id,
  tool_call_id,
  capability_id,
  media_ordinal
)
```

This key identifies one admitted media member of one immutable committed result. Content hash is integrity evidence, not the primary replay/ownership key.

Required behavior:

```text
no prior durable F7-T projection for F7TSourceKey
-> one canonicalization claimant may proceed

durable READY projection already exists for F7TSourceKey
-> return/reuse existing asset identity
-> ZERO new ingest attempts

another live canonicalization claim exists
-> loser does not ingest
-> fail closed or observe the canonical winner under separately frozen bounded rules

prior outcome is ambiguous / projection state cannot prove SAFE retry
-> ZERO automatic re-ingest
-> fail closed
```

Restart, recovery, duplicate event delivery, process crash, repeated scanner pass, and reprocessing MUST NOT silently create a second canonical asset for the same F7TSourceKey.

The exact durable reservation/uniqueness mechanism is frozen in Section 17 below. This contract still does not authorize the table, migration, repository, service, or runtime implementation.

## 8. One-ingest-attempt and side-effect terminality

For one F7TSourceKey there may be at most one authorized `AssetService.ingest_stream(...)` attempt after a durable exclusive claim is established.

After ingest starts:
- cancellation is terminal for that claim unless durable state proves no canonical asset was committed;
- storage/finalize ambiguity is not retry authority;
- process loss is not retry authority;
- no tool replay, re-execution, provider retry/fallback, capability fallback, or UBQ re-admission may be used to regenerate bytes;
- no second ingest may occur merely because the first caller did not observe its return value.

`AssetService.ingest_stream()` remains the only current CAS storage/finalize primitive. F7-T MUST NOT duplicate FileAsset/FileBlob SQL/ObjectStorage lifecycle logic.

## 9. Canonical identity and origin

Transient tool/provider/base64/path/URL/sandbox identity is never canonical durable identity.

Future successful publication is:

```text
FileAsset.origin_type = "TOOL"
AssetDescriptor.uri = asset://<asset_id>
canonical durable media identity = asset://<asset_id>
```

`origin_id` must bind to the stable F7TSourceKey/provenance chosen by the independently audited production design. It must not be a provider URL, local path, sandbox path, object key, random retry token, or caller-controlled string.

## 10. MIME, filename and byte bounds

Before the first CAS ingest attempt, deterministic validation must complete:

- authenticated canonical owner is proven;
- full durable result eligibility is proven;
- exact media ordinal/source envelope is proven;
- declared MIME is from an admitted media class and passes normalization/allow-list rules frozen by production PRE-CLAIM;
- filename is metadata only, sanitized, bounded, and cannot affect storage identity;
- known content length, when available, is non-negative and does not exceed canonical server CAS ingest maximum;
- streamed observed bytes must be bounded by `AssetStorageSettings.max_upload_bytes`;
- empty/invalid payload policy must be explicit per admitted media class;
- source transport must not escape its separately granted retrieval authority.

The server CAS ingest bound remains `AssetStorageSettings.max_upload_bytes`. Client render-memory limits are not F7-T ingestion authority.

## 11. Failure, cancellation and incomplete-source rules

All validation failures before the exclusive canonicalization claim produce ZERO CAS ingest attempts.

Any failure before `AssetService.ingest_stream()` begins is side-effect-free with respect to CAS.

After ingest begins, F7-T relies on AssetService's existing STAGING/READY/ERROR behavior and MUST NOT infer READY deletion, compensating delete, physical GC, provider cleanup, or rollback authority.

A failed or ambiguous F7-T derivation does not change the already-COMMITTED Agent result. The result remains immutable source truth even if canonical media publication fails.

## 12. UBQ and tool execution boundary

F7-T occurs after committed tool outcome authority and creates no:
- tool retry/replay/re-execution;
- capability re-dispatch;
- UBQ re-admission, recharge, settlement rewrite, refund, or budget mint;
- Agent execution terminalization;
- lease/ResumeClaim/checkpoint/recovery mutation.

The committed side effect is consumed. CAS failure is terminal to F7-T derivation and cannot be converted into permission to run the tool again.

## 13. #156, #165 and #166 boundary

Issue #156 capability routing/sandbox remains separate. PR #157 is contract-only and grants no F7-T production authority.

WEB-DL-1 #165 remains RESERVED / NOT CLAIMED.

Issue #166 CAS-B1 remains RESERVED / HARD HOLD. F7-T does not authorize:
- sandbox/provider-download -> CAS persistence;
- generic file download;
- sandbox filesystem persistence;
- provider remote object fetch;
- lifecycle bridge;
- deletion/GC/provider cleanup.

If a future #156/#165/#166 source produces bytes for F7-T, that source must first receive its own retrieval/persistence authority and then satisfy this F7-T committed-result/idempotency contract. Authority does not transfer by adjacency.

## 14. Current source evidence and drift guard

This freeze is based on `main@522b543e66f309085729e57a2248258b7c4f2599`.

Current evidence includes:
- `se/src/infrastructure/storage/models/sql/agent/tool_result.py` for durable result fields and COMMITTED/PROVISIONAL state;
- `docs/context_future/CTX_F5_3I_B1_TOOL_RESPONSE_PAYLOAD_SOURCE_AUTHORITY.md` for durable lineage, canonical Session.user_id owner proof, and exact committed output authority;
- `se/src/application/assets/service.py::AssetService.ingest_stream(...)` for current CAS storage/finalize authority and non-idempotent new-ID allocation;
- `docs/central_asset/CAS_F7_0_GENERATED_MEDIA_CANONICALIZATION_CONTRACT_ECB7E5DC.md` for F7-T deferral after Agent COMMITTED authority;
- landed F7-S evidence for continued separation of provider-stream media from tool-result persistence.

Before any production PRE-CLAIM transition, refresh #74, current main, #107, #147, #156/#157, #165, #166, and any active PR touching AgentToolResult persistence, capability/tool execution, CTX TOOL_RESPONSE_PAYLOAD, AssetService, or CAS schema.

Any semantic movement in those boundaries is MATERIAL until independently classified otherwise.

## 15. Production PRE-CLAIM exit questions

Production PRE-CLAIM may PASS only when a fresh independent audit freezes all of:

1. exact typed tool-media envelope/source classes;
2. exact post-COMMITTED trigger;
3. exact durable F7-T projection/reservation representation;
4. uniqueness/idempotency/concurrency behavior for F7TSourceKey;
5. exact origin_id/provenance encoding;
6. exact source byte acquisition authority;
7. MIME/filename/max-byte validation;
8. one-ingest-attempt/cancellation/process-loss behavior;
9. projection publication and consumer lookup surface;
10. restart/recovery/reprocessing duplicate suppression;
11. CTX opaque committed-output coexistence;
12. #156/#165/#166 non-overlap;
13. zero tool replay/fallback and zero UBQ authority expansion;
14. exact bounded production file list and regression matrix;
15. no blocking P0/P1 and explicit disposition of any P2.

Until then:

```text
F7-T production implementation = CLOSED
F7-T production CLAIM = NONE
schema/migration authority = NONE
runtime/wiring authority = NONE
merge authority = NONE
```


## 16. P1 remediation freeze — first source class and durable admission binding

This section closes the **design ambiguity** identified as
`P1-CAS-F7T-SOURCE-CLASS-AUTHORITY-1` without acquiring capability-routing,
Tools V1, #156, or producer implementation authority.

### 16.1 First and only F7-T source class in the initial production design

The first F7-T media source class is exactly:

```text
source_contract_id = F7T_INLINE_BASE64_V1
source_transport = INLINE_BASE64_IN_COMMITTED_RESULT
external retrieval = NONE
URL/path/provider/sandbox dereference = FORBIDDEN
```

The media bytes MUST already be fully contained in the immutable successful
COMMITTED `AgentToolResultRecord.output`. F7-T does not fetch, open, resolve,
download, or follow any path/URL/provider/sandbox handle.

The only admitted envelope is the already-canonical Tools V1 successful
`ToolResult` shape, with the F7-T media object carried only inside `data`:

```json
{
  "ok": true,
  "tool": "<canonical non-empty tool name>",
  "action": "<canonical non-empty action>",
  "data": {
    "$f7t_media": {
      "contract": "F7T_INLINE_BASE64_V1",
      "items": [
        {
          "ordinal": 0,
          "media_kind": "image",
          "mime_type": "image/png",
          "filename": "generated.png",
          "encoding": "base64",
          "size_bytes": 123,
          "sha256": "<64 lowercase hex>",
          "data_base64": "<canonical base64>"
        }
      ]
    }
  },
  "error": null,
  "meta": {
    "version": "<non-empty>",
    "truncated": false,
    "warnings": []
  }
}
```

For this first source class:
- the outer object must satisfy the canonical Tools V1 ToolResult success branch;
- `ok` is exactly `true`;
- `error` is exactly `null`;
- `meta.truncated` is exactly `false`; truncated tool output is never media
  admission authority;
- `data` keys are exactly `{"$f7t_media"}`;
- `$f7t_media` keys are exactly `{"contract", "items"}`;
- `contract` is exactly `F7T_INLINE_BASE64_V1`;
- `tool`, `action`, and `meta` are validated ToolResult fields but have no
  owner/routing/admission authority beyond proving the canonical envelope;
- `items` is a non-empty ordered list with a bounded count frozen by the later
  production CLAIM;
- every `ordinal` is an integer equal to its zero-based list position;
- `encoding` is exactly `base64`;
- `data_base64` must decode canonically with strict validation;
- decoded byte length must equal `size_bytes`;
- SHA-256(decoded bytes) must equal the lowercase `sha256` field;
- no URL, path, provider_file_id, sandbox identifier, object key, remote handle,
  or alternate byte transport field is permitted.

A generic ToolResult with arbitrary `data`, a media-looking object outside
`data.$f7t_media`, or a failed/truncated ToolResult remains ordinary opaque
tool JSON and is not an F7-T source.

### 16.2 Durable capability contract binding — no mutable catalog lookup

Envelope shape alone is not authority.

Admission additionally requires the durable `CapabilityInvocationRecord`
already referenced by `result.invocation_id` to prove:

```text
invocation.invocation_id == result.invocation_id
invocation.capability_id == result.capability_id
invocation.execution_id == result.execution_id
invocation.tool_call_id == result.tool_call_id
invocation.kind == "TOOL"
invocation.state == "COMPLETED"
invocation.capability_version is canonical non-empty
```

F7-T MUST NOT determine source authority from the current mutable capability
catalog or from a newly loaded `CapabilityDefinition.output_schema`.

Instead the production design uses a **CAS-owned append-only enrollment
registry** keyed only by the durable invocation tuple:

```text
(capability_id, capability_version)
    -> source_contract_id
```

Rules:
- no wildcard capability ID;
- no wildcard version;
- no implementation_id/connection_id fallback;
- no output-shape auto-enrollment;
- an existing key's `source_contract_id` is immutable;
- unknown key = NOT ADMITTED / ZERO CAS mutation;
- the registry controls CAS admission only; it does not alter routing,
  authorization, capability visibility, retries, execution mode, or #156
  target/fingerprint semantics.

This makes the durable invocation's already-persisted exact
`capability_id + capability_version` the source-contract binding. No
output-schema snapshot is required for F7-T source admission.

### 16.3 Initial enrollment fence

PR #240 does not enroll any concrete capability/version pair.

```text
F7T_INLINE_BASE64_V1 protocol = FROZEN
initial admitted capability/version entries = EMPTY
active media ingest from tool results = ZERO until separate enrollment release
```

A future capability-specific enrollment must name the exact
`capability_id + capability_version`, prove that producer intentionally emits
the exact `F7T_INLINE_BASE64_V1` envelope, and receive any required owner
handoff. Adding an enrollment is MATERIAL and cannot be inferred from this
contract.

This empty initial registry is deliberate: it lets the reservation/projection
infrastructure consume the existing Tools V1 ToolResult contract without
falsely granting media authority to all generic `output: Any` tool results.

## 17. P1 remediation freeze — durable F7-T reservation/projection

This section closes the design ambiguity identified as
`P1-CAS-F7T-DURABLE-RESERVATION-1`.

### 17.1 Exact durable record

The future production persistence object is exactly one new SQL record class:

```text
ToolMediaAssetProjectionRecord
table = cas_f7_t_tool_media_projections
```

Required durable columns:

```text
id                  string primary key
source_result_id    FK agent_tool_results.id / RESTRICT / not null
execution_id        string / not null
invocation_id       string / not null
tool_call_id        string / not null
capability_id       string / not null
capability_version  string / not null
media_ordinal       integer / not null / >= 0
source_contract_id  string / not null
owner_user_id       string / not null
origin_id           string / not null
state               RESERVED | INGESTING | READY | AMBIGUOUS
asset_id            FK files.id / RESTRICT / nullable
revision            integer / not null / >= 0
created_at           timestamp / not null
updated_at           timestamp / not null
```

The database MUST enforce one composite uniqueness constraint:

```text
UNIQUE(
  source_result_id,
  invocation_id,
  tool_call_id,
  capability_id,
  media_ordinal
)
```

and one unique `origin_id` constraint inside the projection table.

The durable source tuple is re-proved before insert. Stored duplicated lineage
fields are immutable audit evidence and MUST equal the current durable Agent
rows whenever read.

### 17.2 Deterministic identifiers

Canonical source-key bytes are UTF-8 canonical JSON with sorted keys and no
insignificant whitespace:

```json
{
  "capability_id": "<capability_id>",
  "invocation_id": "<invocation_id>",
  "media_ordinal": 0,
  "source_result_id": "<result_id>",
  "tool_call_id": "<tool_call_id>"
}
```

```text
source_key_digest = lowercase hex sha256(canonical source-key bytes)
projection.id     = "f7tp_" + source_key_digest
origin_id         = "f7t:v1:" + source_key_digest
```

`origin_id` is passed unchanged to `AssetService.ingest_stream(...,
origin_type="TOOL", origin_id=origin_id)`.

No provider ID, local path, URL, random retry token, or caller-selected value
participates in these identities.

### 17.3 Exact state machine and at-most-once ingest

The state machine is:

```text
(no row)
  -- validated source + successful INSERT -->
RESERVED
  -- revision-CAS winner, durably committed before AssetService call -->
INGESTING
  -- AssetService returns READY asset + projection revision-CAS -->
READY

INGESTING
  -- local ingest call raises/cancels after call began -->
AMBIGUOUS
```

No other transition is authorized in the first production slice.

Required behavior:
1. all source/envelope/owner/size/MIME/hash checks occur before the INSERT;
2. unique INSERT conflict loads the existing projection instead of calling
   `AssetService`;
3. only the process that wins `RESERVED -> INGESTING` may call
   `AssetService.ingest_stream()`;
4. `INGESTING` is committed durably **before** the first ingest call;
5. observing pre-existing `INGESTING` after restart/process loss means
   ambiguous prior side effect and therefore ZERO automatic re-ingest;
6. `AMBIGUOUS` is terminal in this slice and therefore ZERO automatic
   re-ingest;
7. `READY` returns/reuses the stored `asset_id` with ZERO new ingest;
8. no stale lease, timeout, process restart, scanner pass, duplicate event, or
   caller retry can transition `INGESTING/AMBIGUOUS` back to `RESERVED`;
9. no F7-T code may delete an orphaned asset or projection row.

A crash after CAS ingest succeeds but before the projection becomes READY may
leave `INGESTING`. That is intentionally fail-closed: this slice prefers one
possible orphan over a duplicate irreversible ingest. Reconciliation/repair of
that state is separate future authority and is not deletion/GC permission.

### 17.4 Exact projection lookup surface

The only first-slice lookup operations are repository-internal:

```text
get_tool_media_projection_by_source_key(...)
try_create_tool_media_projection_reservation(...)
compare_and_set_tool_media_projection(...)
```

There is no new HTTP route, client API, model-callable tool, CTX source type,
search index, Memory surface, or #156 capability.

### 17.5 Exact first production persistence/migration maximum

A later independent PRE-CLAIM may release at most this persistence/design set
for the first infrastructure slice:

```text
NEW    se/src/infrastructure/storage/models/sql/assets/tool_media_projection.py
MODIFY se/src/infrastructure/storage/models/sql/assets/__init__.py
NEW    se/src/infrastructure/storage/migrations/sql/versions/27a_cas_f7_t_tool_media_projection.py
MODIFY se/src/infrastructure/storage/repositories/assets.py
NEW    se/src/application/assets/tool_generated_media.py
NEW    se/tests/application/assets/test_cas_f7_t_tool_generated_media.py
```

Frozen migration lineage on the currently audited baseline:

```text
revision      = 27a_cas_f7_t_tool_media_projection
down_revision = 26a_ubq2_dual_accounting_bridge
```

Any new migration landing on canonical main before CLAIM is MATERIAL drift and
requires re-freezing the migration lineage. No `se/src/runtimes/agent/**`,
`se/src/main.py`, schema outside the new projection table, client code,
#156 routing, or #166 bridge is included in this first persistence slice.

Because the initial capability enrollment registry is empty, this first slice
can be tested without invoking CAS ingest from live Agent execution. A later
separate activation/enrollment PRE-CLAIM must freeze the exact post-COMMITTED
caller/wiring before any live tool result can trigger F7-T.

## 18. P1 disposition after this amendment

This contract candidate now freezes concrete answers for both blocking P1
questions from audit #5979725767:

```text
P1-CAS-F7T-SOURCE-CLASS-AUTHORITY-1:
  design ambiguity = ADDRESSED
  first source class = F7T_INLINE_BASE64_V1
  durable binding = exact CapabilityInvocationRecord capability_id/version
                    + CAS-owned append-only exact enrollment registry
  current concrete capability enrollment = EMPTY
  capability/routing authority transfer = NONE

P1-CAS-F7T-DURABLE-RESERVATION-1:
  design ambiguity = ADDRESSED
  table = cas_f7_t_tool_media_projections
  unique source key = exact five-field tuple
  state machine = RESERVED -> INGESTING -> READY | AMBIGUOUS
  automatic re-ingest after ambiguous/restart state = FORBIDDEN
```

Only a fresh independent audit may mark either P1 CLOSED for production
PRE-CLAIM. This owner amendment does not self-approve production authority.

The remaining activation/enrollment wiring, exact producer enrollment, and any
future ambiguity reconciliation stay outside this two-file candidate and
require later explicit release.
