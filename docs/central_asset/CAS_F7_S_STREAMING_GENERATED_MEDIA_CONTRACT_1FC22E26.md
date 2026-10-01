# CAS-F7-S — Streaming Generated-Media Canonicalization Contract Freeze

Status: **CONTRACT / PRE-CLAIM PREPARATION / ZERO-PRODUCTION**

Primary workspace: Issue #74
Canonical governance: Issue #85 v2.5
Exact baseline: main@1fc22e2624aaedddb11885c780c1b45b7602321e
Prior landed stage: CAS-F7-P1 / PR #180 / post-merge Architecture #1710 GREEN/GREEN (attempt 2)

## 1. Authority and scope

~~~text
production/runtime/schema/migration delta = ZERO
F7-P1 non-stream = LANDED / CANONICAL / HEALTHY
F7-S contract/PRE-CLAIM preparation = OPEN
F7-S production CLAIM = CLOSED
F7-T tool-generated media = CLOSED
CAS-F8 = CLOSED
READY deletion / GC / provider cleanup / session regeneration = CLOSED
#166 CAS-B1 = HARD HOLD
merge authority = NONE
~~~

F7-S must reuse the landed F7-P1 application-owned CAS authority:
- existing context.container.asset_service only;
- existing context.config.assets.max_upload_bytes read-only;
- canonical identity remains asset://<asset_id>;
- provider/base64/URL/fileData identity is transient transport, never canonical identity.

F7-S acquires no provider routing/model-selection, breaker, retry/fallback, Agent tool-result, R11/R12, CTX, READY deletion, provider cleanup, physical GC, or sandbox/CAS-B1 authority.

## 2. Current-main stream facts

Current ChatExecutionHandler.stream_with_fallback(...):
- executes ProviderExecutor.execute_stream(...);
- sets stream_started = True immediately before yielding each public chunk;
- once a public chunk has been yielded, later provider failure is terminal and does not fallback;
- contains no generated-media response canonicalization fence on the stream path.

Current Gemini ResponseChats.adapt_chat_stream(...):
- processes only obj["candidates"][0];
- calls _parse_gemini_parts_to_content(...) without preserve_generated_file_data=True;
- may lower streaming fileData.fileUri through the legacy URL path;
- emits parsed attachment information through GatewayStreamChunk.metadata.content_parts;
- exposes only text/reasoning/tool-calls through GatewayStreamDelta.

Current GatewayStreamDelta has no attachment/media field. F7-S must not invent a raw base64/provider URL text substitute.

## 3. Core invariant

A partial provider chunk is not durable generated-media authority.

~~~text
partial provider chunk
!= complete logical provider response
!= response-wide generated-media cardinality
!= terminal generated object
!= CAS ingest authority
~~~

No generated-media bytes, base64, provider URI, provider file id, or ambiguous URL representation may become public/durable/model-visible as a substitute canonical result.

## 4. Provider-neutral terminal envelope / assembler

Before any F7-S CAS ingest, one provider-neutral stream assembler must preserve enough information to prove:
1. complete logical provider-stream termination;
2. all candidates relevant to generated-media cardinality;
3. deterministic choice identity and selected choice 0;
4. every generated-media object identity and provenance;
5. response-wide generated-media cardinality;
6. response-wide tool-call presence;
7. deterministic terminal generated-object boundary;
8. complete bounded inline bytes for an admitted source;
9. MIME/type metadata without provenance loss;
10. authenticated owner_user_id;
11. cancellation state;
12. provider attempt identity and terminality state.

The assembler is application/provider-neutral. Provider converters may preserve provenance but must not call CAS persistence directly.

## 5. First F7-S source-class scope

The first future F7-S production slice is no broader than landed F7-P1 source admission.

Admitted:
- complete inline generated media with exactly one authoritative inline payload;
- no simultaneous URI/provider-file identity;
- bounded under existing max_upload_bytes.

Still CLOSED:
- generated fileData / remote provider handle;
- generic URL transport;
- provider download/resolver;
- object-storage remote handle;
- multiple generated objects;
- tool-generated media.

Generated fileData must preserve provider-generated provenance in the stream assembler and fail terminally as a CLOSED source class. It must not degrade into ordinary UrlContent.

Ordinary URL content remains pass-through and must not increment generated-media cardinality.

## 6. Public streaming emission fence

Normal text-only streaming remains incremental pass-through and does not require full-response buffering while no generated-media candidate has been observed.

Once the first generated-media candidate is observed:

~~~text
generated_media_seen = True
=> provider attempt becomes fallback-terminal
=> raw generated-media transport MUST NOT be yielded
=> generated-media-bearing metadata MUST be withheld/sanitized
=> assembler continues until provider stream terminal completion
~~~

Text/reasoning already emitted before generated-media observation may remain visible under normal stream semantics. F7-S does not promise transactional rollback of already-visible text.

After media observation, any public chunk emitted before final CAS decision must be proven free of generated-media transport identity.

## 7. Final commit point

The first CAS ingest is prohibited until provider stream completion and all deterministic pre-ingest checks pass.

~~~text
provider stream begins
-> preserve candidate/provenance/cardinality facts
-> optionally emit transport-safe text/reasoning
-> provider stream terminal completion
-> selected choice 0
-> response-wide tool_calls == NONE
-> generated-media cardinality == 1
-> admitted inline source + unambiguous transport
-> authenticated owner
-> not cancelled
-> persistence available / max_upload_bytes configured
-> exactly one AssetService.ingest_stream(...)
-> canonical READY asset
-> emit canonical asset identity only
~~~

Cardinality greater than one fails closed before first ingest. No atomic-batch or compensation/rollback authority is inferred.

## 8. Canonical stream output

Successful output contains only canonical asset identity plus non-authoritative display metadata:
- no raw/base64 payload;
- no provider URI/provider_file_id;
- source="asset";
- canonical asset_id / asset://<asset_id>;
- no duplicate generated-media emission.

GatewayStreamDelta currently has no attachment field. The first F7-S implementation must use an existing schema-compatible canonical projection whose exact representation is independently frozen before production CLAIM. Any new public stream schema field requires explicit re-freeze and client-contract audit.

## 9. Tool-call terminality

Generated media is terminal assistant-response authority only.

If any tool call is observed anywhere in the relevant logical streamed response:

~~~text
generated-media CAS ingest = PROHIBITED
CAS ingest attempts = ZERO
READY assets = ZERO
~~~

This prevents commitment from an intermediate tool-loop response.

## 10. Provider fallback / breaker boundary

Before any public output or generated-media observation, ordinary provider-stream fallback semantics remain unchanged.

After either first public output or first generated-media observation, the provider attempt is fallback-terminal.

Provider success followed by F7-S validation/CAS failure must not:
- trigger provider fallback;
- trigger model reselection/regeneration;
- count as provider breaker failure.

## 11. Cancellation boundary

Cancellation before the F7-S commit point:
- aborts the assembler;
- performs ZERO CAS ingest;
- emits no canonical asset identity.

F7-S does not acquire READY deletion/rollback authority. Production must re-check cancellation immediately before first ingest and must not depend on post-ingest compensation.

If cancellation races after AssetService.ingest_stream(...) begins, existing AssetService failure/finalization semantics remain authoritative. F7-S exposes no asset reference unless ingest returns canonical READY success.

## 12. Error classes

Future production must distinguish at least:
- incomplete/invalid stream envelope;
- cardinality > 1;
- media on non-selected choice;
- media with tool calls;
- source class CLOSED;
- ambiguous inline transport;
- owner missing;
- persistence unavailable;
- too large;
- invalid inline bytes;
- cancelled before commit.

All deterministic pre-ingest rejection paths require ZERO CAS ingest.

## 13. Bounded future production paths

Expected F7-S production touch set:
- se/src/application/assets/generated.py or a sibling provider-neutral stream assembler;
- se/src/provider/handlers/chat_handler.py;
- se/src/provider/gemini/converters/chats/response.py;
- focused tests/evidence.

No production authority is granted for:
- se/src/application/container.py;
- se/src/main.py;
- AssetService/storage/UoW construction;
- schema/migration;
- provider routing policy;
- AgentToolResult persistence;
- sandbox/CAS-B1;
- destructive lifecycle/GC.

If implementation needs paths outside this set, STOP and re-freeze.

## 14. Regression matrix

A future implementation must prove:
1. pure text stream remains incremental;
2. ordinary URL stream passes through / ZERO ingest;
3. generated inline media never leaks before terminal commit;
4. one valid inline object -> exactly one ingest -> canonical asset-only projection;
5. generated fileData remains provenance-preserved and CLOSED / ZERO ingest;
6. two generated objects across chunks -> reject / ZERO ingest;
7. media then later tool call -> reject / ZERO ingest;
8. media on non-selected candidate -> reject / ZERO ingest;
9. ambiguous inline transport -> reject / ZERO ingest;
10. persistence unavailable -> reject / ZERO ingest;
11. cancellation before commit -> ZERO ingest;
12. provider failure after media observation -> no fallback;
13. CAS failure after provider completion -> no fallback/breaker blame;
14. F7-P1 regressions remain GREEN;
15. F5 request-side projection remains unchanged.

## 15. Cross-track disposition

- Issue #15 / CTX owns Memory/context authority, not provider-response media commitment.
- Issue #107 / AE-R12 owns Agent recovery/lease semantics, not F7-S CAS commitment.
- Issue #156 transfers no CAS lifecycle authority.
- Issue #166 / CAS-B1 remains HARD HOLD because F7-T/tool-output persistence remains CLOSED.
- UBQ accounting/timeout stages do not become F7-S storage authority.

## 16. PRE-CLAIM release gate

F7-S production CLAIM remains CLOSED until independent audit on an exact contract/evidence HEAD confirms:
- exact canonical main and health;
- provider-neutral assembler ownership;
- complete-response/candidate/provenance preservation;
- public emission fence;
- terminal commit point;
- cardinality/tool-call/cancellation rules;
- first-slice source classes;
- exact canonical output representation;
- bounded future production paths;
- regression matrix;
- cross-track disposition;
- no blocking P0/P1/P2.

This contract does not authorize production implementation or merge.
