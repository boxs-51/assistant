# CAS-F7-T-A0 — Activation / Enrollment Boundary Freeze

Status: **CONTRACT / ARCHITECTURE EVIDENCE CANDIDATE / ZERO-PRODUCTION**

Primary workspace: Issue #74  
Canonical governance: Issue #85 v2.5  
Independent roadmap release: Issue #74 comment #5987579119  
Frozen development baseline: main@d967165a5c8cda6ffaf9a172f14b9dc0696bf140  
Baseline Architecture: #2092 / 37224467909 / GREEN-GREEN

~~~text
stage = CAS-F7-T-A0
mode = CONTRACT + ARCHITECTURE EVIDENCE ONLY
production/runtime/schema/migration delta = ZERO
production CLAIM = NONE
production activation PRE-CLAIM = HOLD
concrete capability enrollment = PROHIBITED
merge authority = NONE

F7-T persistence = LANDED / CANONICAL / HEALTHY
migration 27a_cas_f7_t_tool_media_projection = CANONICAL
production enrollment registry = EMPTY
live Agent F7-T trigger/wiring = ABSENT

#165 WEB-DL-1 = RESERVED / NOT CLAIMED
#166 CAS-B1 = RESERVED / HARD HOLD / NO CLAIM
F8 = CLOSED
READY deletion / lifecycle / GC / provider cleanup / reconciliation = CLOSED
~~~

## 1. Purpose

A0 freezes the smallest safe boundary between the landed F7-T persistence
infrastructure and any future live tool-result activation.

A0 grants no runtime authority. It exists to make a later production A1
PRE-CLAIM finite and auditable without inferring authority from the presence of
ToolGeneratedMediaCanonicalizer, the 27a projection table, a media-looking tool
payload, or a mutable capability catalog.

The landed F7-T persistence infrastructure remains dormant in production:
- the default enrollment is EMPTY_F7T_ENROLLMENT;
- no production caller invokes canonicalize_committed_result(...);
- no production tool/capability intentionally emits the exact
  F7T_INLINE_BASE64_V1 envelope;
- no Agent, CTX, UBQ, #156, #165 or #166 authority transfers into CAS by
  adjacency.

## 2. Current-source facts frozen by A0

On the A0 baseline:

1. se/src/application/assets/tool_generated_media.py contains:
   - F7T_INLINE_BASE64_V1;
   - EMPTY_F7T_ENROLLMENT = MappingProxyType({});
   - ToolGeneratedMediaCanonicalizer;
   - canonicalize_committed_result(source_result_id=...);
   - strict inline-base64, hash, filename/path and durable-lineage checks;
   - per-item max_media_bytes validation;
   - durable RESERVED -> INGESTING -> READY | AMBIGUOUS projection semantics.

2. The production repository contains no tool/capability producer that emits
   data.$f7t_media with contract F7T_INLINE_BASE64_V1. Occurrences outside
   contract/tests are limited to the CAS parser itself.

3. AgentRuntime persists transport results through DurableAgentStore and then
   loads COMMITTED durable results before they can enter model context. There
   is no live F7-T canonicalizer dependency or post-COMMITTED F7-T call in
   AgentRuntime on this baseline.

4. Application bootstrap constructs AssetService but does not construct or bind
   ToolGeneratedMediaCanonicalizer.

These facts are not permission to activate the path. They are the reason A0 is
required.

## 3. Blocking P1 disposition

Immediate production activation remains blocked.

~~~text
P0 = NONE FOUND

P1-CAS-F7T-ACT-PRODUCER-1 = OPEN / GAP-HOLD
  no concrete producer capability/version emits F7T_INLINE_BASE64_V1

P1-CAS-F7T-ACT-BOUNDS-1 = DESIGN FROZEN BY A0
  live count/aggregate/media-kind/MIME policy is frozen below but not implemented

P1-CAS-F7T-ACT-TRIGGER-1 = DESIGN FROZEN BY A0 / FRESH AGENT-OWNER RELEASE STILL REQUIRED
  both durable post-COMMITTED Agent read seams are frozen below; AgentRuntime authority is not transferred

P1-CAS-F7T-ACT-FAILURE-1 = DESIGN FROZEN BY A0
  live failure isolation is frozen below but not implemented

production A1 PRE-CLAIM = HOLD
~~~

A0 may land with P1-CAS-F7T-ACT-PRODUCER-1 still OPEN. A1 may not be CLAIMed
until that GAP is closed by an explicit producer-owner handoff on then-current
main.

The independent FINAL HOLD in Issue #74 comment #5988013554 also requires this
candidate to freeze three activation fences before replacement FINAL:

~~~text
P1-CAS-F7T-A0-AGENT-OWNER-1 = DESIGN FROZEN / REPLACEMENT FINAL REQUIRED
P1-CAS-F7T-A0-BUDGET-ISOLATION-2 = DESIGN FROZEN / REPLACEMENT FINAL REQUIRED
P1-CAS-F7T-A0-TRIGGER-COVERAGE-3 = DESIGN FROZEN / REPLACEMENT FINAL REQUIRED
~~~

These design freezes close no production gate by themselves. A1 remains
NOT CLAIMABLE until the then-current canonical AgentRuntime owner/workspace,
the producer owner, exact-head Architecture, and replacement independent audit
all pass their separate gates.

## 4. Exact first activation producer gate

A1 must name exactly one first producer tuple:

~~~text
(capability_id, capability_version) -> F7T_INLINE_BASE64_V1
~~~

Before A1 PRE-CLAIM can PASS, independent evidence must prove all of:

- the exact capability_id is canonical and non-empty;
- the exact capability_version is canonical and non-empty;
- producer-owner authority intentionally emits the exact successful Tools V1
  ToolResult envelope frozen by the landed F7-T contract;
- data keys are exactly {"$f7t_media"};
- contract is exactly F7T_INLINE_BASE64_V1;
- producer output is not inferred from output_schema shape, catalog metadata,
  provider response shape, implementation_id, connection_id, filename, MIME,
  URL, path, sandbox identifier or base64-looking JSON;
- producer behavior is canonical/healthy before CAS A1 consumes it.

No wildcard ID, wildcard version, mutable-catalog lookup, output-shape
auto-enrollment or fallback enrollment is permitted.

Current A0 disposition:

~~~text
first producer tuple = UNRESOLVED
producer owner handoff = ABSENT
concrete enrollment entry = NONE
P1-CAS-F7T-ACT-PRODUCER-1 = GAP / HOLD
~~~

The owner MUST NOT invent a tuple merely to make A1 claimable.

## 5. Frozen live admission bounds for the first A1 design

Once a real producer handoff exists, the first A1 activation design is limited
to the following CAS-owned admission policy.

~~~text
source_contract_id = F7T_INLINE_BASE64_V1
max_media_items = 8
per_item_decoded_bytes <= config.assets.max_upload_bytes
aggregate_decoded_bytes <= config.assets.max_upload_bytes

admitted media_kind = image
admitted MIME =
  image/png
  image/jpeg
  image/webp
~~~

Rules:

1. items must be a non-empty list with 1 <= len(items) <= 8;
2. each item independently remains subject to the landed strict base64,
   declared size and SHA-256 checks;
3. the sum of decoded payload lengths across all items must not exceed the
   same server-owned config.assets.max_upload_bytes ceiling;
4. aggregate validation completes before the first AssetService ingest call;
5. media_kind is exactly image for A1;
6. MIME is normalized to lowercase ASCII and must be exactly one of the three
   values above;
7. filename remains metadata only and has no identity/path authority;
8. no sniffed MIME, extension-derived MIME, wildcard image/* or producer
   override is accepted;
9. changing the count, aggregate bound, media kind or MIME set is MATERIAL and
   requires a new PRE-CLAIM.

A0 does not change AssetStorageSettings and does not add a new quota. It reuses
the existing server CAS max_upload_bytes as the upper bound for both a single
admitted item and the complete decoded F7-T envelope.

## 6. Exact post-COMMITTED trigger coverage

The future A1 trigger is a read-only Agent sidecar after durable commitment is
proven.

Current AgentRuntime has exactly two durable COMMITTED read seams that may
observe a result for F7-T publication:

~~~text
AgentRuntime._load_committed_tool_result(...)
AgentRuntime._load_committed_tool_result_by_id(...)
~~~

A1 must route BOTH seams through one shared post-COMMITTED scheduling hook:

~~~text
AgentRuntime._schedule_f7t_publication_for_committed_result(
    source_result_id=<durable AgentToolResultRecord.id>
)
~~~

Each seam may invoke that hook only after its durable loader returned the exact
AgentToolResultRecord, commit_state == COMMITTED has been proved, and the seam's
execution/tool-call/invocation identity checks have passed. The hook is invoked
before the seam returns its model-facing ToolExecutionResult, but the hook only
schedules publication; it does not wait for publication completion.

Every ordinary execution, resume, recovery, REUSE_COMMITTED or repeated durable
read observation that passes through either seam MUST schedule using the SAME
durable record.id as source_result_id. Duplicate observation is permitted only
because the landed F7-T projection is idempotent/fail-closed by durable source
key.

The trigger MUST NOT run:

- before DurableAgentStore.save_tool_result(...);
- inside the SQL transaction that commits AgentToolResultRecord;
- on a transport-only ToolExecutionResult that has not been re-read as
  COMMITTED;
- on PROVISIONAL or OUTCOME_UNKNOWN state;
- from CapabilityRuntime before Agent commitment;
- from provider/runtime response handling;
- from CTX source assembly;
- from a continuation result before its durable COMMITTED re-read;
- from a startup scanner, catalog walk or recovery scanner that invents a new
  tool outcome.

No third durable-load seam may silently activate F7-T. If AgentRuntime later
adds another model-consumable COMMITTED read seam, that is MATERIAL to A1 and
requires a fresh PRE-CLAIM unless the shared hook coverage is independently
re-proved.

The shared hook passes only the already committed result identity to:

~~~text
ToolGeneratedMediaCanonicalizer.canonicalize_committed_result(
    source_result_id=<durable AgentToolResultRecord.id>
)
~~~

The canonicalizer remains responsible for re-reading and re-proving the exact
durable source lineage. AgentRuntime does not pass owner_user_id, media bytes,
asset_id, MIME authority or CAS projection state into the canonicalizer.

## 7. Duplicate observation / resume / recovery semantics

The live sidecar may observe the same COMMITTED result more than once because
ordinary execution, resume, recovery or repeated durable reads can converge on
the same source result.

That is allowed only because the landed F7-T persistence contract is
idempotent/fail-closed by durable source key.

Required A1 behavior:

- both _load_committed_tool_result(...) and
  _load_committed_tool_result_by_id(...) observations use the same durable
  AgentToolResultRecord.id as source_result_id;
- every repeated observation of that record uses that same durable
  source_result_id;
- READY projection -> reuse asset identity / ZERO new ingest;
- RESERVED observed by a non-winner -> ZERO ingest;
- pre-existing INGESTING -> ZERO automatic re-ingest;
- AMBIGUOUS -> ZERO automatic re-ingest;
- no resume/recovery path may reset projection state;
- no Agent retry/replay path may be invoked because F7-T was rejected, failed,
  cancelled, ambiguous or unavailable.

A1 must not create a new scanner, retry queue or reconciliation process.

## 8. Failure isolation from Agent authority

The already-COMMITTED Agent tool result is authoritative even when F7-T
canonicalization fails.

A1 must preserve:

~~~text
AgentToolResultRecord.output = immutable
AgentToolResultRecord.commit_state = COMMITTED
tool success/failure semantics = unchanged
tool retryability = unchanged
Agent continuation authority = unchanged
CTX TOOL_RESPONSE_PAYLOAD source = unchanged
UBQ admission/settlement/refund = unchanged
~~~

Expected F7-T rejection or ambiguity is a CAS-side publication outcome only.

A1 MUST NOT:
- rewrite/downgrade the committed tool result;
- convert a successful committed tool result into an Agent failure;
- trigger tool replay/re-execution/fallback;
- create provider retry/fallback;
- mint/re-admit/refund UBQ;
- mutate checkpoint/ResumeClaim/lease authority;
- replace CTX tool-response content with asset:// identity.

### Publication execution / Agent-budget isolation

A1 publication uses one execution model only: a non-gating supervised
AgentRuntime-owned background task created by the shared scheduling hook after
the durable COMMITTED read.

Required execution semantics:

- the Agent caller does NOT await AssetService.ingest_stream() or
  canonicalize_committed_result() before model continuation;
- the publication task is NOT wrapped by AgentRuntime._await_contextual(...);
- no Agent active-budget deadline, iteration deadline, cancellation_event,
  retry budget or continuation budget is passed into the CAS publication task;
- scheduling itself must not mint, consume, extend, shorten or reset any Agent
  execution/iteration budget;
- model continuation proceeds after successful scheduling and never waits for
  publication success, rejection, ambiguity or failure;
- AgentRuntime owns every publication Task in an explicit in-memory task set;
  an unowned fire-and-forget Task is prohibited;
- every Task exception is observed by an owner callback and logged/recorded on
  the CAS publication side; it must never propagate into the already-COMMITTED
  Agent outcome or model continuation;
- normal per-execution Agent cancellation does not cancel an already scheduled
  publication for a COMMITTED result and does not transfer cancellation
  authority to CAS;
- application shutdown/quiesce explicitly stops new F7-T scheduling, cancels
  outstanding runtime-owned publication Tasks, and awaits/gathers them before
  CAS dependencies are torn down;
- shutdown cancellation is handled by the landed canonicalizer cancellation
  rule and projects AMBIGUOUS where possible.

The future shutdown hook is owned by AgentRuntime and invoked from the existing
main.py application lifespan shutdown sequence. If safe task ownership,
exception observation and shutdown/quiesce cannot be implemented inside the
frozen runtime.py + main.py production paths, the A1 six-path maximum is
INVALIDATED and A1 PRE-CLAIM must stop rather than silently add paths.

This execution model proves publication latency cannot gate model continuation
and cannot become Agent timeout/continuation failure. Agent cancellation and
budget authority remain owned by Agent; CAS obtains no new execution-budget or
cancellation authority.

## 9. Service composition and configuration source

A1 must not introduce a new persistence/config subsystem.

The future composition is frozen as:

- uow_factory: existing application/eventing UoW factory;
- asset_service: existing AssetService created by main bootstrap;
- per-item/aggregate byte ceiling: existing config.assets.max_upload_bytes;
- enrollment: immutable exact mapping owned by the CAS F7-T module;
- media-kind/MIME allowlist: immutable first-activation policy owned by the CAS
  F7-T module;
- AgentRuntime receives an optional canonicalizer dependency at construction;
- AgentRuntime owns the supervised F7-T publication task set and a bounded
  shutdown/quiesce method;
- main.py lifespan invokes that quiesce method before CAS dependencies are
  torn down;
- if AssetService is unavailable, no live F7-T canonicalizer is composed and
  no enrollment is activated.

No environment-driven wildcard enrollment and no dynamic capability-catalog
auto-enrollment is authorized.

## 10. Exact future A1 maximum

A0 itself changes only this document and its architecture evidence.

If, and only if, the producer GAP and a fresh bilateral release from the
then-current canonical AgentRuntime owner/workspace are independently closed,
a future A1 PRE-CLAIM may release at most the following six paths:

Production:
1. MODIFY se/src/application/assets/tool_generated_media.py
2. MODIFY se/src/runtimes/agent/runtime.py
3. MODIFY se/src/main.py

Evidence:
4. MODIFY se/tests/application/assets/test_cas_f7_t_tool_generated_media.py
5. MODIFY se/tests/architecture/test_cas_f7_t_activation_enrollment_boundary.py
6. NEW se/tests/integration/test_cas_f7_t_live_activation.py

No ApplicationContainer field is required: bootstrap may construct the
canonicalizer and inject it directly into AgentRuntime.

No schema, migration, repository, AssetService, provider runtime, capability
routing, client, CTX, UBQ or #156 file is included.

Any need for a seventh path is a PRE-CLAIM invalidation and requires a fresh
independent audit.

## 11. Bilateral AgentRuntime-owner authority boundary

Issue #74 owns:
- CAS enrollment admission;
- F7-T envelope validation;
- count/byte/media/MIME policy;
- durable projection/idempotency semantics;
- AssetService publication.

The then-current canonical AgentRuntime owner/workspace owns:
- tool execution outcome truth;
- durable COMMITTED transition;
- invocation/result identity;
- resume/recovery/replay authority;
- Agent cancellation, execution-budget and continuation semantics.

Issue #107 is COMPLETE / CLOSED / CANONICAL HEALTHY. Its landed contracts remain
historical Agent authority evidence, but #107 closure is NOT a standing future
production release and does not make #107 the automatic owner of a later
AgentRuntime edit. #107 may become an authority source again only if governance
explicitly reopens it.

Issue #156 likewise transfers no AgentRuntime or F7-T production authority by
adjacency.

Therefore A1 cannot modify se/src/runtimes/agent/runtime.py until a fresh
bilateral audit/release from the then-current canonical AgentRuntime
owner/workspace explicitly confirms that both durable read seams use the shared
post-COMMITTED scheduling hook and that the supervised publication model does
not move Agent outcome, retry, cancellation, budget or continuation authority
into CAS.

A0 is not that production release.

## 12. Producer-owner boundary

CAS does not own implementation of the first producer.

If the first producer requires any production code change to emit
F7T_INLINE_BASE64_V1, that change must be independently owned/CLAIMed by the
producer's canonical track and must land canonical/healthy before CAS A1
enrollment consumes it.

A1's six-path maximum intentionally contains no producer implementation file.

## 13. #156 / #165 / #166 boundary

A0 and future A1 remain inline-only.

Explicitly CLOSED:
- arbitrary URL retrieval;
- provider URL/file ID retrieval;
- local path reads;
- sandbox:// reads;
- object-key reads;
- signed URL fetch;
- web.download;
- #156 target/routing/fingerprint semantics;
- #165 WEB-DL-1;
- #166 sandbox/provider-download -> CAS bridge.

#166 is not the next production CAS slice. It still depends on its own upstream
WEB-DL-1/#165 readiness plus a fresh #74/#156 overlap release.

## 14. Lifecycle / destructive boundary

A0 and A1 grant no authority for:

- READY FileAsset deletion/release;
- FileBlob deletion;
- physical GC;
- orphan cleanup;
- AMBIGUOUS reconciliation;
- INGESTING repair;
- provider remote cleanup;
- projection deletion;
- migration downgrade while durable projections exist;
- lifecycle backfill;
- CAS-F8.

A possible orphan after ambiguous ingest remains preferable to an unauthorized
duplicate ingest. Repair/reconciliation remains a separate future stage.

## 15. A1 PRE-CLAIM exit gate

A1 production PRE-CLAIM may PASS only when all are true:

1. exact current main and Architecture are healthy;
2. F7-T persistence remains canonical/healthy;
3. exact first producer capability_id + capability_version is named;
4. producer owner handoff proves intentional exact F7T_INLINE_BASE64_V1 output;
5. producer implementation is already canonical/healthy or otherwise separately
   authorized under its owning track;
6. no mutable catalog/output-shape inference is used;
7. max_media_items=8 is preserved;
8. aggregate decoded bytes <= config.assets.max_upload_bytes is preserved;
9. admitted kind/MIME set is exactly the A0 image allowlist;
10. a fresh bilateral release from the then-current canonical AgentRuntime
    owner/workspace accepts the shared post-COMMITTED scheduling hook;
11. both _load_committed_tool_result(...) and
    _load_committed_tool_result_by_id(...) use that same hook after durable
    COMMITTED proof and pass the durable record.id as source_result_id;
12. publication is supervised/non-gating, is not wrapped by
    _await_contextual(...), consumes no Agent execution/iteration budget, and
    cannot gate model continuation;
13. application shutdown/quiesce owns, observes and cancels/gathers outstanding
    publication Tasks before CAS dependency teardown;
14. the call occurs outside the Agent result commit transaction;
15. COMMITTED result semantics remain unchanged on all F7-T outcomes;
16. duplicate/resume/recovery observations cannot produce a second ingest;
17. exact six-path maximum remains sufficient for the frozen supervision model;
18. #156/#165/#166 remain non-overlapping;
19. F8/lifecycle/deletion/GC/provider cleanup/reconciliation remain closed;
20. blocking P0/P1 = NONE.

Until then:

~~~text
CAS-F7-T-A0 = contract/evidence only
CAS-F7-T-A1 = HOLD / NOT CLAIMABLE
concrete enrollment = EMPTY
live Agent activation = CLOSED
merge authority for production = NONE
~~~

## 16. A0 acceptance

A0 is acceptable when:
- candidate changes exactly two NEW files;
- production/runtime/schema/migration delta is zero;
- architecture evidence proves the current dormant baseline and contract fences;
- exact-head Linux + Windows Architecture are GREEN;
- an independent A0 FINAL/PRE-CLAIM audit confirms no authority expansion.

Landing A0 does not itself make A1 claimable. The unresolved producer GAP and
fresh bilateral release from the then-current canonical AgentRuntime
owner/workspace remain hard gates.
