# Central Asset Storage F1→F8 × CTX-F Dependency Audit

**Repository:** `boxs-51/assistant`  
**Audit date:** 2026-09-23  
**Role:** Issue #15 primary planning/audit  
**Scope:** read-only production audit + docs-only CTX planning  
**Status:** FUTURE / PARKED — no production wiring authorized

## 1. Snapshot audited

Current refs observed during this audit:

```text
main
3560a2d034d25659aa535ba91b90aa48256f5ac1

feature/central-asset-storage-f1
1f88641c6161efd0f07c8cc675fbee4344e11fe8

feature/context-memory-personalization-future
a80f5a144a0295cbf220da25502cf60942e0bfd7

active AE-R10 line
work/ae-r10-d-3560a2d0
ecceea58a4e838e0b0407745bac445b54756bf93
```

Central Asset vs current main:

```text
merge-base: a55e4fd2a20ddccbd227e770d26fae72e33bc88e
asset branch: ahead 15 / behind 312
main changed since merge-base: 134 files
asset changed since merge-base: 51 files
exact file overlap: 2

overlap:
- se/src/application/container.py
- se/src/main.py
```

This is favorable for future replay/porting, but the asset branch is still far too stale to use directly as a new implementation baseline.

Current R10 overlap with the parked asset delta:

```text
se/src/provider/handlers/chat_handler.py
```

Therefore F5 provider hydration must be re-audited after R10-H freezes provider routing/retry/streaming behavior.

## 2. Existing Central Asset authority

Authoritative parked sources:

```text
feature/central-asset-storage-f1

docs/CENTRAL_ASSET_STORAGE_HEAD_CHECKPOINT_F60B8D22.md
docs/CENTRAL_ASSET_STORAGE_F5_0_CONTRACT_FREEZE_4B99F679.md
```

Frozen historical implementation:

```text
F1     CLOSED
F2     GREEN
F2-H   CLOSED
F3     CLOSED
F4     CLOSED
F5-0   CONTRACT FROZEN / NOT IMPLEMENTED
F5-1+  PAUSED
F6     NOT STARTED
F7     NOT STARTED
F8     NOT STARTED
```

The Central Asset documents record the combined R7 + asset baseline `4b99f679` as green with:

```text
Phase 5 consolidated: 40 passed
Linux full suite:     830 passed, 1 skipped
Windows client:       68 passed
```

Those results are historical evidence only. They are not evidence for current `main@3560a2d0` or any post-R10 baseline.

## 3. Frozen authority model

The following separation remains correct and must survive CTX-F:

```text
FileAsset
= stable logical file/media identity

FileBlob
= canonical physical bytes

FileReference
= durable usage/reference authority

FileProviderBinding
= transient provider-specific cache/binding

ObjectStorage
= physical byte backend abstraction

AgentToolResult
= tool execution/result authority

Context projection/index
= derived and rebuildable

Memory
= durable learned/recalled information with provenance

ContextSnapshot
= immutable record of what one inference saw
```

Hard rule:

```text
SOURCE AUTHORITY
!=
STORAGE OBJECT
!=
RETRIEVAL PROJECTION
!=
MODEL WORKING SET
```

## 4. Phase-by-phase audit

### F1 — Persistence representation

Current implemented authority is structurally sound:

- `files` owns stable `asset_id`;
- `file_blobs` owns canonical byte locator/integrity;
- `file_references` owns Message/Session/Project/AgentToolResult usage;
- `file_provider_bindings` keeps provider identity secondary.

CTX-F dependencies:

- CTX-F0 must freeze FileAsset/FileBlob/FileReference as source authorities;
- CTX-F2 source locators should expose `asset://<asset_id>`;
- CTX-F3 discovery may index asset metadata/content projections;
- CTX-F4 read must authorize through canonical asset/reference scope;
- CTX-F5 Memory may retain provenance to asset sources;
- CTX-F9 ContextSnapshot may record an asset locator plus immutable content version.

Important versioning rule:

```text
FileAsset.revision
= lifecycle/CAS revision

FileBlob.sha256 + blob_id
= canonical content version evidence
```

Do NOT use `FileAsset.revision` as the CTX content revision. Current message/tool-reference pinning intentionally performs READY→READY CAS and increments FileAsset.revision without changing bytes. Using that field as model-visible content version would create false snapshot invalidation/drift.

### F2 — ObjectStorage + AssetService

The ObjectStorage interface is reusable by CTX-F1 ToolResponsePayload at the physical layer:

```text
put_stream
open_stream
stat
exists
delete
presign_get
```

But reuse must not merge domain authority.

Required CTX-F1 rule:

```text
Central Asset object keyspace / GC ownership
!=
ToolResponsePayload object keyspace / GC ownership
```

Shared driver is allowed; shared lifecycle authority is not.

### F2-H — R7 compatibility hardening

The following invariants align directly with CTX-F:

- PROVISIONAL AgentToolResult cannot create durable asset visibility;
- COMMITTED tool results may reference assets;
- asset pin/delete uses revision fencing;
- logical delete is separated from physical GC;
- stale STAGING cleanup fences the writer before object deletion;
- checkpoints retain stable `asset_id`, never provider identity.

CTX-F inherits these rules for Memory extraction, retrieval and branch promotion.

### F3 — Canonical `/v1/assets`

F3 is an external asset access API, not Context authority.

CTX-F must never turn any of these into durable identity:

```text
signed URL
local path
object key
provider URI
provider file ID
base64
```

Context locators remain logical `asset://...` references with canonical authorization performed before materialization.

### F4 — Canonical Message/Session asset integration

F4 is the most important implemented prerequisite for CTX-F.

Verified architecture:

- durable Message content preserves structured multimodal parts;
- client-supplied metadata is replaced with canonical DB metadata;
- Message + FileReference is one SQL authority transaction;
- foreign asset references roll back;
- edit swaps references atomically;
- live WAITING/RUNNING/CREATED executions pin referenced bytes;
- provider dispatch rejects unhydrated `asset://` content before F5;
- ContextEngine dual-reads Central Asset plus legacy Attachment until F8.

CTX-F must consume canonical structured Message content rather than flattening assets into strings.

Hardening requirement before Context write tools are added:

Current low-level `AssetRepository.create_reference()` blocks MESSAGE_CONTENT and AGENT_TOOL_RESULT bypasses, but still permits SESSION_RESOURCE / PROJECT_RESOURCE insertion without an owner-scoped dedicated authority. No current public exploit was demonstrated in this audit, but future CTX tools must not call that generic primitive directly. Add/retain an application authority that validates trusted owner/session/project scope before durable resource references are created.

### F5 — Provider asset hydration

F5 remains NOT IMPLEMENTED.

The parked audit already identified the core blockers:

```text
provider_file_id nullable reservation requirement
one logical binding slot per asset/provider/namespace
binding revision/CAS + lease fencing
expiry-aware ACTIVE lookup
hydration read lease
trusted inference principal propagation
true async ObjectStorage → provider streaming
provider lifecycle/expiry metadata
provider copy cleanup/reconciliation
canonical provider converter handling
```

F5 sequencing remains:

```text
F5-0 persistence/lease representation
F5-1 trusted inference context
F5-2 AssetHydrationService
F5-3 async FileProvider stream contract
F5-4 Gemini binding
F5-5 inline/converter canonicalization
F5-6 fallback/retry/delete/reconciliation
F5-7 exit gate
```

R10 dependency:

F5-6 must consume the final R10 retry/fallback authority; it must not create a second retry budget or fallback loop. Hydration is per selected provider attempt; provider chat retries reuse that provider hydration; fallback restarts from the immutable canonical body.

### F6 — Client/UI unified asset flow

F6 currently has only a high-level scope and no exact contract freeze.

This is a planning gap, not a current production defect.

Before F6 implementation freeze:

- upload UI must treat `asset_id` as the only durable client identity;
- UI must not persist provider/native file identifiers;
- READY vs STAGING/ERROR state must be explicit;
- legacy attachment compatibility behavior must be declared;
- retries must not create duplicate logical assets silently;
- upload/list/delete must preserve owner scope and F3 semantics.

F6 is not a hard prerequisite for CTX backend architecture, but it is required before the final user-facing CTX/Central-Asset exit gate.

### F7 — Tool/provider-generated media ingestion

F7 currently has no exact contract freeze. This is a material CTX dependency.

Required freeze before implementation:

```text
tool-generated file/media
  -> canonical FileAsset/FileBlob
  -> AGENT_TOOL_RESULT FileReference
  -> visible only after AgentToolResult COMMITTED

provider-generated assistant media
  -> canonical FileAsset/FileBlob
  -> canonical assistant Message reference
  -> provider URI/file ID never durable transcript authority
```

Branch resolution rule:

- creating/storing bytes is not Memory promotion;
- DISCARDED/CANCELLED branch output must not automatically become durable Memory;
- only final authoritative/adopted durable sources become Memory candidates.

F7 is a hard prerequisite for complete CTX discovery/memory coverage of generated media, but CTX core work may begin before F7 if generated-media coverage is explicitly marked incomplete.

### F8 — Legacy attachment cutover/backfill/cleanup

F8 currently has no exact contract freeze.

Required before legacy removal:

- idempotent backfill mapping legacy attachment → canonical `asset_id`;
- content hash/integrity verification;
- owner/session/project scope preservation;
- no duplicate canonical asset creation on retry;
- durable mapping/tombstone for migration diagnostics;
- dual-read remains active until backfill coverage is proven;
- rollback/compatibility policy is explicit;
- Context indexes are rebuilt/reconciled after canonicalization.

CTX-F4 may support dual-read before F8, but CTX-F12 final cleanup/quality gate must either require F8 completion or explicitly retain legacy compatibility as a supported mode.

## 5. Cross-roadmap findings

### [P0 ACTIVATION BLOCKER] CAS-CTX-1 — parked asset branch cannot be implementation baseline

`feature/central-asset-storage-f1` is currently ahead 15 / behind 312 vs main.

Required:

```text
post-R14 main
-> re-anchor/replay Central Asset F1-F4
-> run combined regressions
-> re-freeze F5-0
-> only then production work
```

Do not merge the parked branch wholesale into a future main without replay/semantic audit.

### [P0 DESIGN BLOCKER] CAS-CTX-2 — F5 read lease is too provider-specific for CTX

Frozen F5-0 currently proposes:

```text
file_asset_leases.lease_type
IN ('PROVIDER_HYDRATION')
```

CTX-F also needs safe byte reads for:

- asset text/media derivation;
- chunk/index generation;
- exact `context.read` materialization;
- possible re-index/rebuild work.

If CTX creates a separate lease/GC fence, deletion authority splits.

Required re-freeze:

```text
one canonical asset read-lease primitive
owned by Central Asset lifecycle

bounded lease types, e.g.
PROVIDER_HYDRATION
CONTEXT_DERIVATION
CONTEXT_READ
```

Exact vocabulary may change, but there must be only one deletion/GC fence authority.

### [P0 DESIGN BLOCKER] CAS-CTX-3 — content-version semantics must not use FileAsset.revision

F4 pin operations advance `FileAsset.revision` even when bytes are unchanged.

CTX-F9 immutable snapshots require a stable content version.

Required contract:

```text
logical locator:
asset://<asset_id>

content evidence:
blob_id
sha256
(optional explicit content_version if future replace-in-place exists)

lifecycle CAS:
FileAsset.revision
```

Never conflate lifecycle revision with model-visible content revision.

### [P1] CAS-CTX-4 — AssetDerivative / AssetChunk ownership is undefined

CTX roadmap names `AssetDerivative/AssetChunk`, but Central Asset F1-F8 does not assign their authority.

Freeze in CTX-F0:

- derivatives/chunks are rebuildable Context projections;
- they reference canonical `asset_id` + source hash/version;
- they never become FileAsset/FileBlob authority;
- deletion/authorization derives from canonical asset visibility;
- stale projection invalidation is hash/version driven.

### [P1] CAS-CTX-5 — ToolResponsePayload physical storage sharing needs namespace/GC ownership

CTX-F1 may reuse ObjectStorage physically.

Before implementation define:

- separate object-key namespace;
- separate SQL lifecycle/retention state;
- separate GC scanner/ownership;
- checksum/integrity contract;
- no AssetService deleting ToolResponsePayload objects and vice versa.

### [P1] CAS-CTX-6 — F6/F7/F8 are under-specified

F5 has a real contract; F6-F8 currently do not.

Required docs before code:

```text
F6 client/UI unified flow contract + regression matrix
F7 generated-media ingestion/commit/branch authority contract
F8 legacy backfill/cutover/rollback contract
```

### [P1] CAS-CTX-7 — resource-reference owner authority must be explicit before Context writes

SESSION_RESOURCE / PROJECT_RESOURCE are legal low-level reference types.

CTX read paths currently enter through an owner-authorized Session, but future Context mutation/pinning tools must use a dedicated trusted owner-scoped authority, not generic repository insertion.

### [P1] CAS-CTX-8 — roadmap phase wording must distinguish CAS dependency from CTX phase ownership

Central Asset F5-F8 remains its own workstream.

Do not rename F5-F8 into CTX phases or absorb asset lifecycle into Context Fabric.

CTX-F consumes Central Asset contracts through explicit handoff gates.

## 6. Dependency matrix

Legend:

```text
HARD   = phase cannot safely close without dependency
SOFT   = useful/integration dependency, parallel work allowed
EXIT   = required for final combined exit gate
NONE   = no direct dependency
```

| Central Asset | Current state | CTX-F0 | CTX-F1 Payload | CTX-F2 Sources | CTX-F3 Discovery | CTX-F4 APIs | CTX-F5 Memory | CTX-F6 Personalization | CTX-F7 Score/Dedup | CTX-F8 Continuity | CTX-F9 Snapshot | CTX-F10 Compact | CTX-F11 DefaultAgent | CTX-F12 Exit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 persistence | CLOSED/parked | HARD | SOFT | HARD | HARD | HARD | HARD provenance | NONE | SOFT | SOFT | HARD locator/hash | SOFT | SOFT | HARD |
| F2 ObjectStorage/AssetService | GREEN/parked | HARD | HARD physical driver only | SOFT | HARD for asset materialization | HARD | SOFT | NONE | SOFT | NONE | SOFT | SOFT | SOFT | HARD |
| F2-H R7 hardening | CLOSED/parked | HARD | HARD COMMITTED rule | HARD | HARD | HARD | HARD promotion rule | NONE | HARD branch safety | HARD | HARD | HARD | HARD | HARD |
| F3 /v1/assets | CLOSED/parked | SOFT | NONE | SOFT | SOFT | SOFT | NONE | NONE | NONE | NONE | NONE | NONE | SOFT UI/direct | EXIT |
| F4 Message/Session integration | CLOSED/parked | HARD | SOFT | HARD | HARD | HARD | HARD provenance | NONE | HARD | HARD | HARD | HARD | HARD | HARD |
| F5 provider hydration | frozen/not implemented | SOFT | NONE | NONE | SOFT shared read lease | SOFT | NONE | NONE | NONE | NONE | SOFT | SOFT | HARD asset inference | HARD |
| F6 client/UI unified flow | not started | NONE | NONE | NONE | NONE | SOFT UX | NONE | NONE | NONE | NONE | NONE | NONE | SOFT | EXIT |
| F7 generated-media ingestion | not started | SOFT | HARD media-vs-payload split | HARD complete source set | HARD generated asset discovery | HARD generated reads | HARD generated-media provenance | NONE | SOFT | SOFT | HARD if seen | SOFT | HARD generated output | HARD |
| F8 legacy cutover | not started | SOFT | NONE | SOFT | SOFT dual-read | SOFT dual-read | SOFT | NONE | SOFT | NONE | SOFT | SOFT | SOFT | HARD or explicit compatibility |

## 7. Recommended execution order

Hard ordering after Agent Execution completes:

```text
R10
 -> R11
 -> R12
 -> R13
 -> R14
 -> unified main
 -> CAS re-anchor/replay F1-F4
 -> combined regression
 -> CAS F5-0 re-freeze
 -> create/activate dedicated Central Asset issue
```

Then use dependency-driven parallelism instead of serializing everything:

```text
                     +-> CAS F5-1..F5-7 -----------+
                     |                              |
post-R14 CAS gate ---+-> CTX-F0 contract freeze    |
                     |      |                       |
                     |      +-> CTX-F1 Payload      |
                     |      +-> CTX-F2 Sources      |
                     |              |               |
                     |              +-> CTX-F3 Discovery
                     |              +-> CTX-F4 APIs |
                     |                              |
                     +-> CAS F6                     |
                     +-> CAS F7 --------------------+-> complete generated-media coverage
                     +-> CAS F8 --------------------+-> remove/close legacy dual-read
```

Then:

```text
CTX-F5 Memory
 -> CTX-F6 Personalization
 -> CTX-F7 Pins/Score/Dedup
 -> CTX-F8 ExecutionContinuityState
 -> CTX-F9 WorkingSet/ContextSnapshot
 -> CTX-F10 CompactContext
 -> CTX-F11 DefaultChatAgent convergence
 -> CTX-F12 combined quality/observability/migration exit
```

Important correction:

Full CAS F5-F8 completion is **not necessarily a hard prerequisite for CTX-F0/CTX-F1**. The hard prerequisite is:

```text
post-R14 baseline
+
Central Asset F1-F4 safely integrated
+
F5-0 cross-roadmap contract re-frozen
```

F5/F6/F7/F8 may then advance with explicit gates. CTX phases that require their behavior must wait at the matrix boundaries above.

## 8. Post-R14 re-freeze checklist

Before any new production write:

1. inspect final main and active migration head;
2. replay/port F1-F4 onto final main without assuming old SHAs apply;
3. audit `container.py` / `main.py` integration conflicts;
4. audit final R10 `chat_handler.py` semantics before F5;
5. audit R11 retention/GC before adding ToolResponsePayload or asset derivatives;
6. audit R12 execution leases against asset read/provider-binding leases;
7. ensure R13 legacy cleanup did not remove compatibility required by F8;
8. add CTX-safe canonical asset read lease types;
9. freeze asset content-version semantics (`blob_id/sha256`);
10. freeze AssetDerivative/AssetChunk ownership;
11. freeze ToolResponsePayload object namespace + GC ownership;
12. write F6/F7/F8 contracts before their production changes;
13. run F1-F4 regressions plus current full CI;
14. only then open staged implementation claims.

## 9. Exit invariants

The combined program must preserve:

```text
CAS-CTX-I01 asset_id is canonical file/media identity.
CAS-CTX-I02 provider file identity is transient cache only.
CAS-CTX-I03 ObjectStorage is physical storage, not domain authority.
CAS-CTX-I04 PROVISIONAL tool output is never durable Context/Memory input.
CAS-CTX-I05 COMMITTED tool output may create canonical references.
CAS-CTX-I06 discarded branch conclusions do not auto-promote Memory.
CAS-CTX-I07 context asset projections are derived and rebuildable.
CAS-CTX-I08 context reads use trusted owner scope.
CAS-CTX-I09 one canonical lease authority fences asset byte reads vs GC.
CAS-CTX-I10 asset lifecycle revision is not asset content revision.
CAS-CTX-I11 ContextSnapshot records stable locator + immutable content evidence.
CAS-CTX-I12 provider fallback never reuses another provider's transformed body.
CAS-CTX-I13 provider retry never re-uploads an already hydrated asset for the same provider attempt group.
CAS-CTX-I14 F7 generated media becomes canonical before durable conversation/context use.
CAS-CTX-I15 F8 legacy removal occurs only after verified backfill/dual-read exit.
CAS-CTX-I16 ToolResponsePayload and Central Asset may share a driver but never GC authority.
CAS-CTX-I17 no subsystem creates a parallel file/context/prompt authority.
```

## 10. Current disposition

```text
F1-F4:
historically GREEN, architecture retained, must be replayed/re-audited on post-R14 main

F5:
contract direction retained; F5-0 must be re-frozen for CTX-safe read leases and post-R10/R11/R12 semantics

F6:
planning contract required

F7:
planning contract required; material CTX provenance dependency

F8:
planning contract required; legacy dual-read exit dependency

CTX-F:
remains docs/audit only
```

No production code change is authorized by this audit.
