# CAS-F7-0 — Generated Media Canonicalization Contract Freeze

Status: **CAS-F7-0 LANDED / F7-P1 PRE-CLAIM COMPOSITION AUTHORITY ADDENDUM CANDIDATE**

Primary workspace: Issue #74  
Canonical governance: Issue #85 v2.5  
Independent release: Issue #74 comment #5854440946  
Replacement audit findings: Issue #74 comment #5854824240  
Final evidence re-anchor: `main@ecb7e5dc5c61aba9772d9f6ebfec303ce8a01755`

F7-0 landing: PR #131 / merge commit `380ce309a9ab8743437e7932c7e859f2160c5c86` / post-merge Architecture #1641 GREEN/GREEN.  
F7-P1 PRE-CLAIM composition re-freeze baseline: `main@9031690d176f0cd82484c83a645b6ced05f21f3b` / Architecture #1642 GREEN/GREEN.  
Current blocking finding: `P1-CAS-F7-P1-COMPOSITION-AUTHORITY-GAP-1`.

## 1. Authority and scope

This document freezes the CAS-F7-0 generated-media convergence contract only.

```text
production/runtime/schema/migration delta = ZERO
CAS-F7-0 contract/evidence preparation = OPEN
first future production slice = F7-P1 / NON-STREAM PROVIDER-RESPONSE MEDIA ONLY
CAS-F7-P1 production CLAIM = CLOSED
tool-generated media production = DEFERRED / CLOSED (future F7-T substage)
CAS-F8 = CLOSED
merge authority = NONE
```

CAS-F6 is LANDED / CANONICAL / HEALTHY. F7 starts from the canonical user-upload and request-side provider-hydration boundaries already landed in F5/F6 and does not redefine them.

The F7 family has two distinct generated-content authorities:

```text
F7-P1 = non-stream, selected-choice, provenance-preserving provider-response generated media canonicalization
F7-S  = streaming provider-response generated media canonicalization (DEFERRED / CLOSED)
F7-T  = tool-generated media canonicalization after Agent COMMITTED authority
```

F7-0 freezes these boundaries, but only the non-stream F7-P1 slice may become the first production PRE-CLAIM after this contract exits. F7-S streaming generated media requires a separate later contract/audit because current stream decoding does not preserve response-wide generated-media cardinality/identity. F7-T requires a separate later contract/audit because AgentToolResult output is already durable before CAS may consume it.

Provider/base64/URL/object-store/tool transport identity is never canonical CAS identity.

## 2. Exact current-main evidence baseline

Final replacement baseline:

```text
canonical main = ecb7e5dc5c61aba9772d9f6ebfec303ce8a01755
prior material-evidence baseline = 3d7fe844a94332fb89c23b5d2984041dc14c3f63
Architecture #1471 / run 36329935424 = GREEN/GREEN
linux-full-suite = SUCCESS
windows-client-contracts = SUCCESS
Issue #134 = CLOSED / RESOLVED BY SUPERSEDING CANONICAL CONTRACT
CAS-F7 production CLAIM = NOT RELEASED
CAS-F8 = CLOSED
```

The material stream rewrite between `3d7fe844...` and the healthy `a524ba87...` lineage is now canonical and externally resolved by Issue #134:
- public `CONTEXT_READY` is removed;
- AgentRuntime no longer publishes `CONTEXT_READY`;
- `AGENT_STREAM_EVENT_NAMES` contains response-progress and tool lifecycle events only;
- `AgentEventName.PROGRESS` is projected publicly as `event_type="agent.response"` on channel `response`;
- tool requested/started/completed/failed events are projected on channel `tool`;
- public docs state that no lifecycle or private model reasoning is sent to the UI.

Later movement from `a524ba87...` through current `ecb7e5dc...` adds R12 index/migration work and reserved roadmap documentation without new Agent stream/provider-response/CAS-ingest semantic changes relevant to F7-P1.

F7-0 treats the public response/tool activity stream as external execution/transport context, never as generated-asset commitment authority.

## 3. Existing canonical CAS ingestion authority

The repository already has canonical CAS ingestion authority in:

`se/src/application/assets/service.py::AssetService.ingest_stream(...)`

That service:
- requires `owner_user_id`;
- creates STAGING FileBlob + FileAsset records;
- streams bytes through ObjectStorage;
- enforces a supplied `max_bytes`;
- records observed `size_bytes` and `sha256`;
- finalizes Blob and FileAsset to READY;
- aborts STAGING ingest on cancellation, oversize and storage/finalize failures;
- returns an `AssetDescriptor` with `asset://<asset_id>`.

F7 MUST reuse this application authority rather than persist FileAsset/FileBlob directly from provider converters or Agent tool executors.

## 4. Server-generated-ingest size authority

F7-generated ingestion uses the **server CAS ingest/storage maximum**, not a client render-memory threshold.

The authoritative configuration is:

`se/src/infrastructure/config/schemas.py::AssetStorageSettings.max_upload_bytes`

Current default:

```text
268_435_456 bytes / 256 MiB
```

Normative rule:

```text
F7 generated_ingest_max_bytes
= current configured AssetStorageSettings.max_upload_bytes
```

The exact configured value MUST be injected/read from canonical server configuration at composition time and passed as `max_bytes` to `AssetService.ingest_stream(...)`.

Provider/protocol limits may reject content earlier, but they MUST NOT raise or bypass the CAS maximum.

The F6 desktop 32 MiB canonical media **render-memory** ceiling is a separate client/UI safety rule and MUST NOT be reused as the server F7 ingestion limit.

Oversized provider-generated content fails closed before it can become a READY canonical asset.

## 5. Current provider-generated-content gap

These are baseline observations, not permanent absence assertions.

Current `se/src/provider/gemini/converters/chats/response.py` converts provider `inlineData` into a `GatewayAttachment` carrying:

```text
base64_data=<provider generated bytes>
source="base64"
```

Provider `fileData` may remain URL/provider transport state. In current Gemini non-stream decoding, extensionless `fileData.fileUri` can be lowered to generic `UrlContent`, which loses generated-media provenance before a provider-neutral post-decoding canonicalizer can distinguish it from an ordinary URL.

Therefore provider-generated binary/media can leave provider decoding without first receiving canonical CAS identity.

Provider converters remain provider decoding/lowering owners. They do not gain CAS persistence authority. For the initial F7-P1 slice, a provider converter/envelope MUST preserve explicit generated-media provenance through successful decoding for every generated source class that downstream F7-P1 preflight must identify. Converter-side rejection of generated `fileData` under the current executor/fallback path is NOT an admitted fail-closed mechanism because converter exceptions are accounted as provider failures and may trigger fallback/regeneration.

## 6. Shared DIRECT + AGENT provider-response boundary

Current DIRECT provider execution returns a decoded `GatewayResponse` from:

`se/src/provider/handlers/chat_handler.py::ChatExecutionHandler.execute_with_fallback(...)`

Current AGENT inference also invokes that same handler from:

`se/src/runtimes/agent/adapters/inference.py::ProviderInferenceAdapter.complete(...)`

and then projects `gateway_message.content` into `InferenceResponse.message.content`.

The future F7-P1 composition point is therefore frozen as:

```text
provider converter
  -> decoded GatewayResponse / GatewayMessage
  -> ProviderExecutor.execute(...) SUCCESS
  -> shared provider-neutral GeneratedAssetCanonicalizer
  -> canonical GatewayResponse
     containing asset_id/source="asset"/asset://...
  -> DIRECT return
  -> ProviderInferenceAdapter -> AGENT InferenceResponse
```

For non-stream responses, canonicalization runs **after provider execution has succeeded** and **before the successful decoded response leaves ChatExecutionHandler**.

F7-P1 MUST NOT persist CAS assets inside Gemini/OpenAI/Ollama-specific converters.

## 7. Post-provider canonicalization is terminal to fallback

Once provider A has successfully produced and decoded a response, F7-P1 canonicalization is outside provider-selection authority.

If:

```text
provider A SUCCESS
 -> generated-media canonicalization
 -> CAS ingest/bound/decode/cancellation failure
```

then the canonicalization failure is **terminal to that logical chat execution**.

It MUST NOT:
- select provider B;
- retry provider generation;
- re-enter provider fallback;
- charge the CAS/storage/canonicalization failure to provider circuit-breaker health;
- mark provider A unhealthy solely because CAS canonicalization failed;
- produce a second generated response/media object.

Implementation must use an error boundary/type that is not interpreted as `ProviderError` or transport failure by provider fallback policy.

This terminality is required for provider cost/side-effect safety and exactly-once generated-response handling.

## 8. F7-P1 canonical identity and owner authority

A provider-generated object admitted by F7-P1 must end as:

```text
FileAsset.state = READY
AssetDescriptor.asset_id = <canonical id>
GatewayAttachment.asset_id = <canonical id>
GatewayAttachment.source = "asset"
GatewayAttachment.uri = asset://<asset_id>
```

The canonical provider-response message representation MUST NOT also retain the same object's:
- inline base64 payload;
- raw bytes;
- provider file ID;
- provider URI;
- object-store key;
- signed URL;
- temporary local path;
- browser object URL.

Owner authority is derived only from the already-authenticated request/execution context.

No provider field, URL, provider file ID, filename, MIME, metadata scalar or client-supplied user field may mint or override `owner_user_id`.

If authenticated owner authority is unavailable at the canonicalization point, F7-P1 fails closed and MUST NOT create an asset.

## 9. Exact origin_type mapping

Current FileAsset schema already permits:

```text
USER_UPLOAD
ASSISTANT
TOOL
PROVIDER_IMPORT
SYSTEM_IMPORT
LEGACY_MIGRATION
```

No schema/migration change is authorized by F7-0.

Exact F7 mapping:

| Source class | FileAsset.origin_type | Status |
|---|---|---|
| provider-response generated media emitted as assistant output | `ASSISTANT` | F7-P1 frozen mapping |
| tool-result generated media | `TOOL` | reserved for future F7-T only; production CLOSED |
| ordinary F6 user upload | `USER_UPLOAD` | existing behavior; not re-ingested by F7 |
| externally imported provider object not generated as current assistant response | `PROVIDER_IMPORT` | outside F7-P1 unless separately released |

Provider name, model, provider response ID and MIME/filename are metadata/provenance only. They do not replace `origin_type`, owner authority or canonical identity.

## 10. F7-P1 ingestion and exactly-once attempt rule

### Response-wide first-slice cardinality fence

Before any CAS ingest, the first F7-P1 production slice MUST perform a response-wide generated-media cardinality preflight over the fully decoded successful NON-STREAM provider response only.

The initial production contract deliberately admits **at most ONE durable generated-media object in the consumer-selected choice of a terminal non-stream assistant response with no tool calls**.

Normative behavior:

```text
0 durable generated-media objects across the full decoded response
-> no generated-media CAS ingest

exactly 1 durable generated-media object
AND it is in consumer-selected choice index 0
AND its source provenance is explicitly preserved and F7-P1-admitted
AND response.choices[0].message.tool_calls is empty
-> terminal consumer-visible assistant response is eligible
-> validate the sole object completely
-> exactly one CAS ingest may begin

any durable generated-media object on a response with one or more tool_calls
-> nonterminal intermediate response in the initial F7-P1 slice
-> fail closed BEFORE first CAS ingest
-> ZERO CAS ingest attempts
-> ZERO READY assets created for that logical response
-> provider success remains consumed; MUST NOT trigger provider fallback/regeneration

any durable generated-media object in choice index >0
-> unsupported in the first F7-P1 slice
-> fail closed BEFORE first CAS ingest
-> ZERO CAS ingest attempts
-> ZERO READY assets created for that logical response

more than 1 durable generated-media object across all choices
-> unsupported in the first F7-P1 slice
-> fail closed BEFORE first CAS ingest
-> ZERO CAS ingest attempts
-> ZERO READY assets created for that logical response
```

The cardinality preflight counts every provider-produced attachment/object whose generated-media provenance is preserved in the decoded response, including multiple Gemini `inlineData` parts or candidates. It is response-level, not per-object.

Current downstream Agent projection is explicit: `ProviderInferenceAdapter.complete(...)` consumes `response.choices[0]`. Therefore the first F7-P1 slice freezes **choice index 0 as the only consumer-selected choice eligible to carry canonicalized generated media**. If any generated-media candidate is present in choice index >0, the response fails closed before the first CAS ingest. F7-P1 MUST NOT create a READY asset that the current consumer projection would immediately discard.

The provider-neutral preflight operates on the decoded `GatewayResponse`, not on the later `InferenceResponse`. The exact terminality fence at this boundary is therefore ordered and structural:

```text
1. require response.choices[0] to exist and be the selected eligible choice
2. selected_choice = response.choices[0]
3. require selected_choice.message.tool_calls to be empty
4. only then may the sole admitted generated-media object proceed toward CAS ingest
```

Normatively, after the selected choice has been validated, the condition is `response.choices[0].message.tool_calls is empty`. `GatewayResponse` itself has no top-level `message` field.

The first slice also freezes **terminal-response eligibility** across both current consumers. This later runtime evidence uses the consumer-facing `InferenceResponse`, where DIRECT appends each inference response but returns it only when `response.message.tool_calls` is empty; otherwise it executes tools and continues the inference loop. AGENT likewise transitions to `FINALIZING` and `COMPLETED` only when `response.message.tool_calls` is empty; otherwise it enters the tool-calling path and continues. These `InferenceResponse.message.tool_calls` checks are supporting consumer-terminal evidence only; they do not replace the provider-neutral `GatewayResponse.choices[0].message.tool_calls` preflight expression. Therefore any generated-media object on a tool-call-bearing response is unsupported in initial F7-P1 and MUST fail closed before first CAS ingest with ZERO CAS ingest attempts / ZERO READY assets. F7-P1 MUST NOT create a READY asset from an intermediate tool-loop response that may never become the final consumer-visible assistant result.

Supporting generated media on tool-call-bearing intermediate responses is deferred. It requires a separate contract for durable intermediate attachment/history semantics and must not be inferred from F7-P1.

For the sole admitted object, all deterministic pre-ingest validation that can be completed without storage side effects MUST complete before `AssetService.ingest_stream(...)` begins: authenticated owner, non-stream complete object boundary, selected choice index 0 exists, `response.choices[0].message.tool_calls` is empty, preserved generated-media provenance, transport decode, MIME/metadata requirements, and configured size bound.

Multi-object canonicalization is deferred to a separate later contract/audit that proves either an atomic batch primitive or independently authorized compensation/rollback authority. F7-P1 MUST NOT infer READY deletion, cleanup or rollback authority to compensate a partially ingested multi-object response.

This fence ensures an unsupported multi-object response cannot leave an earlier READY asset orphaned when a later object fails. Provider success remains consumed: cardinality rejection or later CAS failure is terminal and MUST NOT trigger provider fallback/regeneration.

Future application composition:

```text
GeneratedAssetCanonicalizer
  -> validate authenticated owner
  -> validate response.choices[0] exists and is the selected eligible choice
  -> require response.choices[0].message.tool_calls empty
  -> validate one complete provider-generated object in choice index 0
  -> require preserved generated-media provenance
  -> decode admitted inline transient provider transport
  -> enforce configured AssetStorageSettings.max_upload_bytes
  -> exactly one AssetService.ingest_stream(
       owner_user_id=<authenticated authority>,
       filename=<metadata only>,
       mime_type=<metadata>,
       stream=<bounded complete bytes>,
       max_bytes=<configured AssetStorageSettings.max_upload_bytes>,
       origin_type="ASSISTANT",
       origin_id=<stable provider-response object id when available>,
       metadata=<non-authoritative provenance>
     )
  -> READY AssetDescriptor
  -> canonical GatewayAttachment
```

`AssetService.ingest_stream` remains the storage/finalize authority. The canonicalizer MUST NOT duplicate its SQL/ObjectStorage protocol.

Current `AssetService.ingest_stream` allocates new asset/blob IDs and is not a content-idempotency primitive.

Therefore the first F7-P1 slice freezes:
- at most ONE durable generated-media object is eligible per terminal successful non-stream assistant response, it must be in choice index 0, and `response.choices[0].message.tool_calls` must be empty;
- response cardinality greater than one fails closed before the first CAS ingest with ZERO CAS ingest attempts;
- generated media on any response with non-empty `tool_calls` fails closed before the first CAS ingest with ZERO CAS ingest attempts / ZERO READY assets;
- one canonicalization pass for the sole admitted generated-media object;
- no automatic CAS canonicalization retry after ingest has begun;
- no provider regeneration on CAS failure;
- ambiguous post-ingest outcome fails closed;
- no second ingest attempt unless a separately audited replay/idempotency authority can prove the prior canonical result;
- multi-object canonicalization requires a separately audited atomic-batch or compensation authority.

A later request initiated by the user is a new logical provider execution and is not treated as an internal F7 retry.

Content hash is storage-integrity evidence, not ownership or replay authority.

## 11. Provider inline bytes and remote/fileData transport

The initial F7-P1 admitted transport class is deliberately narrow.

For provider `inlineData` or an equivalent decoded attachment that preserves explicit generated-media provenance and complete bounded inline bytes:
- base64/inline bytes are transient source transport;
- decode failure is terminal/fail-closed;
- decoded bytes are ingested to CAS once;
- the returned provider-response message substitutes canonical asset identity for the transient binary transport.

For provider `fileData`, generic URL or remote handle in the initial F7-P1 slice:
- support = EXCLUDED / CLOSED;
- a URL string alone is not canonical history identity;
- F7-P1 MUST NOT silently treat a generic `UrlContent` as ordinary non-generated content when the provider source was generated `fileData`;
- the response MUST ALWAYS fail closed before the first CAS ingest in the initial F7-P1 slice, even when generated-media provenance is preserved;
- preserved provenance allows the provider-neutral preflight to identify the excluded source class and reject it deterministically; it does NOT admit that source class;
- provenance preservation != admission;
- provenance preservation != canonicalization authority;
- rejection of this excluded source class produces ZERO CAS ingest attempts / ZERO READY assets;
- no raw provider URI may escape as durable message/history identity or as a substitute for canonical CAS identity.

Current Gemini non-stream `adapt_chat(...)` also uses `_parse_gemini_parts_to_content(...)`, where extensionless `fileData.fileUri` may become indistinguishable `UrlContent`. Under the current execution topology, converter-side rejection is not a safe terminal boundary: `ProviderExecutor.execute(...)` accounts generic converter exceptions through `breaker.on_failure()`, and `ChatExecutionHandler.execute_with_fallback(...)` may consume the resulting `ProviderError` and continue to another provider.

Therefore the initial F7-P1 compatibility path is singular and mandatory:
1. preserve generated `fileData` provenance/identity through successful provider decoding;
2. allow `ProviderExecutor.execute(...)` to complete successfully without charging breaker failure;
3. run the shared provider-neutral generated-media preflight after provider SUCCESS and outside provider fallback;
4. identify `fileData` / generic URL / remote handle as EXCLUDED / CLOSED and reject terminally before any CAS ingest.

For initial F7-P1, converter-side rejection before provenance loss is NOT an allowed implementation shortcut. If a future design wants provider-layer rejection, it requires a separately frozen terminal exception boundary that is independently proven not to affect provider breaker health and not to trigger fallback/regeneration.

Preservation determines where the downstream terminal rejection can occur. It does not make `fileData`, generic URL or remote handle eligible for F7-P1 ingestion. A separate future source-class release is required before any of those transports may be canonicalized.

That compatibility change remains provider decoding/envelope authority only; it does not grant the converter CAS persistence authority.

Provider remote cleanup/delete remains CLOSED.

## 12. Streaming generated-media boundary — deferred F7-S

The initial F7-P1 production slice is **non-stream only**.

```text
F7-P1 non-stream provider-response generated media = eligible for first production slice
F7-S streaming generated media = DEFERRED / CLOSED
```

Current `ChatExecutionHandler.stream_with_fallback(...)` yields provider chunks as they arrive. Current Gemini stream decoding is insufficient for the response-wide cardinality/identity guarantee required by F7-P1:
- `ResponseChats.adapt_chat_stream(...)` selects only `obj["candidates"][0]`, so additional candidates are not preserved for a downstream provider-neutral preflight;
- extensionless Gemini `fileData.fileUri` may be lowered to generic `UrlContent`, so generated-media identity may become indistinguishable from ordinary URL content after decoding.

Therefore no streaming generated-media response is eligible for CAS canonicalization under the first F7-P1 production slice. The first slice MUST NOT claim support for streaming generated media and MUST NOT rely on a downstream canonicalizer to reconstruct candidate cardinality or media identity after those details have been discarded.

The existing public Agent stream remains response/tool activity only:

```text
agent.progress -> public event_type = agent.response / channel = response
agent.tool.requested|started|completed|failed -> channel = tool
public lifecycle events -> not emitted to UI

partial provider chunk
!= terminal generated object
!= durable FileAsset authority

public Agent response/tool activity event
!= generated object commitment authority
```

A future F7-S streaming release must first freeze and independently prove a provider-neutral terminal envelope/assembler that preserves, before any CAS ingest:
- the complete logical provider response boundary;
- all provider candidates relevant to generated-media cardinality;
- every generated-media object identity, including extensionless remote/fileData forms;
- response-wide generated-media cardinality;
- deterministic terminal object boundaries;
- complete bounded bytes or an authenticated resolver capable of obtaining them;
- MIME/type classification without losing generated-media identity;
- authenticated owner authority;
- cancellation state.

Only after those facts are preserved may a streaming generated object become eligible for CAS canonicalization.

For future F7-S, cardinality greater than one must still fail closed before first CAS ingest unless a separately audited atomic-batch/compensation authority is released. Provider success followed by CAS canonicalization failure remains terminal and MUST NOT trigger provider fallback/regeneration.

No partially assembled FileAsset may become READY or model/history visible under F7 authority.

## 13. Tool-generated media boundary — deferred F7-T

Agent authority is normative:

```text
AgentToolResult.commit_state != COMMITTED
=> MUST NOT establish durable/model-visible canonical asset references
```

Current Agent persistence makes `AgentToolResult.output` durable JSON and COMMITTED projection terminal/immutable.

That creates a representation problem when a COMMITTED output itself embeds base64/binary transport: CAS cannot canonicalize before COMMITTED, and F7 is not allowed to rewrite the immutable committed result after COMMITTED.

Therefore:

```text
F7-P1 tool-generated media support = NONE
F7-T tool-generated canonicalization = DEFERRED / CLOSED
```

F7-0 does **not** authorize this flow:

```text
COMMITTED raw binary/base64 AgentToolResult
 -> post-hoc rewrite of AgentToolResult.output
```

A future F7-T release must independently choose and prove a compatible authority, such as:
- an Agent-owned canonical projection/handoff after COMMITTED but before separate model/history projection, without mutating immutable `AgentToolResult.output`; or
- a supported tool-result envelope that durably stores only a stable resolvable source reference rather than embedded binary/base64.

Until that contract exists, tool-generated binary/media cannot enter CAS under F7 production authority.

F7 never sets/promotes `commit_state`, changes invocation reconciliation, or changes R6/R7/R12 execution semantics.

## 14. Durable message/history boundary

For F7-P1 provider-response media, canonicalization completes before the generated media is accepted into durable message/history/model-visible state.

The F7-P1 canonical message representation carries canonical asset identity plus non-authoritative display metadata only.

It does not durably retain provider/base64 transport identity for the same canonicalized provider-response object.

This exclusivity rule is scoped to F7-P1 provider-response message/history representation. It does not claim authority to rewrite immutable AgentToolResult audit evidence; tool-generated dual-representation is why F7-T remains CLOSED.

Session regeneration remains CLOSED.

## 15. Failure semantics

F7-P1 non-stream fails closed on:
- more than one durable generated-media object in one non-stream provider response;
- any durable generated-media object outside consumer-selected choice index 0;
- any generated-media object on a nonterminal response with non-empty `tool_calls`;
- unsupported provider fileData/generic URL/remote-handle generated media, regardless of whether provenance is preserved;
- missing authenticated owner;
- incomplete/ambiguous generated object;
- invalid base64/byte transport;
- content exceeding configured `AssetStorageSettings.max_upload_bytes`;
- AssetService ingest/storage/finalize failure;
- any attempt to treat streaming generated media as eligible under the first F7-P1 slice;
- cancellation before terminal canonicalization;
- ambiguous ingest replay without proven canonical result.

Failure MUST NOT:
- perform any CAS ingest when response cardinality is greater than one;
- perform any CAS ingest when generated media exists outside choice index 0;
- perform any CAS ingest when the response has generated media and non-empty `tool_calls`;
- return unsupported generated fileData as an ordinary durable/raw URL substitute;
- expose provider/base64 identity as substitute durable canonical message state;
- fall back to legacy `/v1/files`;
- create partial READY FileAsset state;
- retry/regenerate through provider fallback after provider success;
- affect provider circuit-breaker health;
- infer deletion/cleanup/GC authority.

## 16. Future production path matrix

This is a **future production-candidate map**, not a production grant.

| Path | Future role | F7-0 classification |
|---|---|---|
| `se/src/application/assets/generated.py` | proposed provider-neutral F7-P1 canonicalizer | EXPECTED NEW / production authority not released |
| `se/src/application/assets/service.py` | existing ingest/finalize authority exposed as application-owned `context.container.asset_service` | EXPECT NO CHANGE / REUSE ONLY |
| `se/src/runtimes/provider/runtime.py` | F7-P1 composition/injection owner inside `ProviderRuntime.initialize(context)` | EXPECTED future F7-P1 composition change; reuse `context.container.asset_service` when available, keep an explicit unavailable-persistence response fence otherwise, pass `context.config.assets.max_upload_bytes` read-only, inject provider-neutral canonicalizer into `ChatExecutionHandler`, preserve existing F5 `asset_projection_hook` independently |
| `se/src/kernel/base.py` | existing `RuntimeContext` dependency boundary exposing `container` and `config` | EXPECT NO CHANGE |
| `se/src/application/container.py` | existing application composition owner exposing `asset_service` | NO CHANGE; F7-P1 MUST NOT add a parallel CAS service or new bootstrap wiring here |
| `se/src/main.py` | application bootstrap / container assembly | NO CHANGE for F7-P1 |
| `se/src/infrastructure/config/schemas.py` | authoritative `AssetStorageSettings.max_upload_bytes` definition consumed via `context.config.assets.max_upload_bytes` | EXPECT NO CHANGE / READ-ONLY INPUT |
| `se/src/infrastructure/storage/models/sql/assets/file.py` | existing valid `origin_type` enum/check | EXPECT NO CHANGE |
| `se/src/provider/handlers/chat_handler.py` | non-stream post-provider response hook and terminal no-fallback boundary | EXPECTED future F7-P1 non-stream change; streaming generated-media path excluded from first slice |
| `se/src/provider/gemini/converters/chats/response.py` | provider decoding/lowering; current non-stream extensionless fileData and stream paths can lose generated-media provenance/cardinality | REQUIRED future F7-P1 compatibility change to preserve non-stream generated fileData provenance through successful decode; downstream provider-neutral preflight performs terminal rejection while fileData remains EXCLUDED/CLOSED; separate REQUIRED future F7-S change for streaming |
| other provider response converters | decoding/lowering only | NON-STREAM audit for F7-P1; streaming eligibility CLOSED until each provider preserves full terminal cardinality/media identity |
| `se/src/runtimes/agent/adapters/inference.py` | AGENT consumer projects response.choices[0] | EXPECT NO CHANGE; F7-P1 preflight must reject generated media in choice index >0 before ingest |
| `se/src/runtimes/chat/direct.py` | DIRECT terminal-response evidence; tool-call responses continue the loop | EXPECT NO CHANGE; F7-P1 preflight must reject generated media when tool_calls is non-empty before ingest |
| `se/src/runtimes/agent/runtime.py` | AGENT terminal-response/tool-loop evidence and execution authority | NO CHANGE; F7-P1 preflight uses empty tool_calls as terminal-response eligibility evidence only |
| `se/src/runtimes/agent/persistence.py` | COMMITTED/PROVISIONAL durable authority | NO CHANGE |
| `se/src/runtimes/agent/stream.py` | canonical public response/tool activity projection | NO CHANGE / NOT ASSET COMMITMENT AUTHORITY |
| `se/src/infrastructure/storage/repositories/agent.py` | Agent durable result repository | NO CHANGE |
| canonical message/history persistence | provider-response canonical identity consumer | CONDITIONAL; fresh audit required if hook placement is not before persistence |
| storage schema/migrations | no F7-0/F7-P1 schema authority | NO CHANGE |
| client/UI F6 paths | landed user-upload/render authority | NO CHANGE |
| F5 request-side hydration/projection | request-only downstream projection | NO CHANGE |

No production file in this table may be edited under F7-0 authority.

### F7-P1 composition / injection authority freeze

The first production PRE-CLAIM MUST use the existing runtime dependency graph rather than minting a parallel bootstrap or CAS authority.

Normative composition path:

```text
ProviderRuntime.initialize(context)
  -> RuntimeContext.container / RuntimeContext.config
  -> observe existing context.container.asset_service readiness
  -> read context.config.assets.max_upload_bytes as read-only input
  -> construct/install provider-neutral GeneratedAssetCanonicalizer boundary
       persistence_state = AVAILABLE(existing application-owned AssetService)
                           OR UNAVAILABLE(explicit no-persistence sentinel/state)
  -> inject that response-side canonicalization boundary into ChatExecutionHandler
       even when persistence_state = UNAVAILABLE
  -> preserve existing CAS-F5 request-side asset_projection_hook readiness/wiring independently
  -> provider execution succeeds
  -> decoded non-stream GatewayResponse reaches shared provider-neutral post-success preflight

     if generated_media_count == 0
       -> ordinary text response passes through unchanged
       -> no CAS ingest is required
       -> ProviderRuntime ordinary text availability is preserved

     if generated_media_count >= 1
       AND persistence_state = UNAVAILABLE
       -> terminal F7 canonicalization-unavailable error AFTER provider success
       -> ZERO CAS ingest attempts / ZERO READY assets
       -> raw/base64/provider URL identity MUST NOT escape as substitute output
       -> NO provider fallback/reselection/regeneration
       -> NO provider breaker failure accounting

     if generated_media_count == 1
       AND persistence_state = AVAILABLE
       -> continue all existing F7-P1 admission/cardinality/terminal-response/source fences
       -> canonicalize through existing application-owned AssetService
       -> only canonicalized asset identity may return
```

Authority rules:
- `ProviderRuntime.initialize(context)` is the frozen F7-P1 **composition/injection owner** for the first production slice.
- `context.container.asset_service` is the only application-owned CAS persistence service that F7-P1 may reuse. F7-P1 MUST NOT construct another `AssetService`, another storage/UoW authority, or another application container.
- `context.config.assets.max_upload_bytes` is a read-only configuration input. F7-P1 does not own `AssetStorageSettings` schema or config bootstrap.
- `se/src/application/container.py` and `se/src/main.py` remain NO CHANGE for the first F7-P1 production slice.
- the landed F5 request-side `asset_projection_hook` readiness/wiring remains unchanged and independent from the new response-side generated-media canonicalizer.
- ProviderRuntime ordinary text inference MUST NOT depend on CAS asset-service readiness. A decoded non-stream response with zero generated-media objects remains ordinary pass-through and requires no CAS ingest.
- the F7-P1 response-side canonicalization boundary MUST remain installed/semantically enforced even when `context.container.asset_service` is unavailable or not ready. It MUST represent that condition explicitly as an unavailable persistence state/sentinel rather than silently omit the response fence.
- generated media present while persistence is unavailable/not-ready is a terminal F7 canonicalization error after provider success and before any CAS ingest. It produces ZERO CAS ingest attempts / ZERO READY assets.
- unavailable persistence MUST NOT permit raw/base64/provider URL identity to return as a substitute canonical result.
- missing/uninitialized `context.container.asset_service` MUST NOT be repaired by constructing a parallel persistence service inside ProviderRuntime.
- canonicalizer failure after provider success, including persistence-unavailable generated media, is terminal F7 failure. It MUST NOT be charged to provider breaker health and MUST NOT cause provider fallback/reselection/regeneration.
- F5 request-side asset projection readiness remains an independent concern; F7-P1 MUST NOT reinterpret F5 request hydration/projection readiness as permission to bypass the response-side generated-media fence.
- this composition freeze opens no F7-S, F7-T, F8, READY deletion/GC, provider cleanup, session-regeneration, bootstrap, or configuration-schema authority.

This addendum closes only the PRE-CLAIM composition-authority gap. It does not itself release F7-P1 production CLAIM.

## 17. Cross-issue dependency disposition

### CTX / Issue #15

CTX retains Memory/promotion/retrieval/source-discovery and ToolResponsePayload authority. CAS does not redefine CTX payload identity or promotion semantics.

Current CTX verifier work is zero-migration and transfers no CAS authority.

### Agent / Issue #107

Agent/R12 retains execution lease/recovery/checkpoint and tool-result commitment authority.

F7-P1 is non-stream provider-response-only and does not depend on modifying AgentToolResult persistence. F7-S streaming generated media remains separately CLOSED.

The COMMITTED fence is retained as the prerequisite for any future F7-T design, but F7-T remains CLOSED.

### Current-main Agent stream authority / Issue #134

Issue #134 is CLOSED with resolution `RESOLVED BY SUPERSEDING CANONICAL CONTRACT`.

Exact current public stream authority is:
```text
CONTEXT_READY public lifecycle event = REMOVED / NOT CANONICAL
public Agent stream = response/tool only
AgentEventName.PROGRESS -> public agent.response
tool requested/started/completed/failed -> public tool activity
lifecycle/private reasoning -> NOT SENT TO UI
```

F7 classification:
```text
public Agent response/tool stream -> F7-P1 non-stream provider-response semantics = NO authority transfer
public Agent response/tool activity event -> CAS generated-asset commitment = NEVER
```

### Reserved Agent asset-grants roadmap

Current main contains:
`docs/central_asset/CAS_AGENT_ASSET_GRANTS_ROADMAP.md`

Its authority is:
```text
State = RESERVED / NOT OPEN
Implementation authority = none
effect on active CAS-F7/F8 authority = NONE
```

F7-0 records this roadmap only as a future reserved dependency. It does not open grant APIs, schema, provider behavior, or any production path.

## 18. Explicitly CLOSED

```text
CAS-F7-P1 production implementation = CLOSED
CAS-F7-S streaming generated media = CLOSED
CAS-F7-T tool-generated media = CLOSED
CAS-F8 legacy cutover/backfill/removal = CLOSED
provider routing/fallback/deadline/model selection = CLOSED
provider capability eligibility / PTC semantics = CLOSED
Agent invocation/tool-result commitment state machine = CLOSED
R12 lease/recovery/checkpoint authority = CLOSED
CTX Memory/promotion/retrieval/source-discovery authority = CLOSED
/v1/files removal/redirect = CLOSED
provider remote cleanup/delete = CLOSED
binding DELETING/DELETED lifecycle = CLOSED
READY FileAsset deletion/release = CLOSED
FileBlob/ObjectStorage physical GC = CLOSED
stale PROCESSING recovery = CLOSED
asset-bearing session regeneration = CLOSED
```

## 19. F7-0 exit gate

A first provider-response production PRE-CLAIM may be considered only when the zero-production candidate proves:

1. exact current main;
2. DIRECT + AGENT shared provider-response ownership;
3. F7-P1 is non-stream provider-response-only;
4. streaming generated media is deferred to separate F7-S authority;
5. tool-generated media is deferred to separate F7-T authority;
6. provider success -> CAS canonicalization failure is terminal/no-fallback/no-breaker;
7. `AssetStorageSettings.max_upload_bytes` is the authoritative server ingestion bound;
8. F6 32 MiB render-memory limit is explicitly not the F7 server ingest bound;
9. `origin_type="ASSISTANT"` for F7-P1 provider-generated assistant media;
10. non-stream complete-object boundary, selected-choice-0 fence, exact `response.choices[0].message.tool_calls` empty terminal-response fence, and response-wide cardinality fence;
11. non-stream admitted source provenance is explicit; generated fileData/URL/remote-handle forms remain EXCLUDED/CLOSED, provenance must survive successful decode, and terminal rejection occurs only in provider-neutral post-success preflight before first CAS ingest;
12. current streaming generated-media exclusion is explicit and future F7-S requires full candidate/media identity preservation;
13. authenticated owner/canonical identity rules;
14. `AssetService.ingest_stream` atomicity/failure behavior;
15. one-ingest-attempt/no-internal-retry rule;
16. exact future production path matrix;
17. F8/destructive/session/provider-routing exclusions;
18. CTX/Agent/current-main dependency disposition;
19. exact current-main health GREEN/GREEN and fresh exact-head Architecture Linux + Windows GREEN;
20. independent replacement audit PASS;
21. blocking F7-0 P0/P1/P2 = NONE;
22. F7-P1 composition authority is frozen to `ProviderRuntime.initialize(context)` reusing `context.container.asset_service` and read-only `context.config.assets.max_upload_bytes`, with `application/container.py` and `main.py` NO CHANGE.

Before that gate:

```text
CAS-F6 = COMPLETE / CANONICAL / HEALTHY
CAS-F7-0 = REPLACEMENT CONTRACT CANDIDATE
CAS-F7-P1 production CLAIM = CLOSED
CAS-F7-S = CLOSED
CAS-F7-T = CLOSED
CAS-F8 = CLOSED
```

Landing this contract does not itself authorize provider, Agent or CAS production edits.
