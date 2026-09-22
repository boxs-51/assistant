# CURRENT STATUS UPDATE — FINAL R7 SYNC COMPLETE

> This file remains the historical F1→F4 pause checkpoint at code baseline `f60b8d22`.
>
> The previously pending final-R7 sync is now complete.
>
> Current combined runtime baseline:
>
> ```text
> 4b99f679e1ea7026215939901a47858b9cc9aae3
> ```
>
> Combined exit gate on that baseline:
>
> ```text
> Phase 5 consolidated: 40 passed
> Linux full suite:     830 passed, 1 skipped
> Windows client:       68 passed
> ```
>
> F5-0 has been re-frozen and remains **NOT IMPLEMENTED**.
>
> Current authoritative F5-0 contract:
>
> ```text
> docs/CENTRAL_ASSET_STORAGE_F5_0_CONTRACT_FREEZE_4B99F679.md
> ```
>
> Any older section below that says "final R7 sync pending" or "re-freeze F5-0 before implementation" is historical context and is superseded by this update.

---

# CENTRAL ASSET STORAGE — HEAD CHECKPOINT & PAUSE DOCUMENT

**Repository:** `boxs-51/assistant`  
**Branch:** `feature/central-asset-storage-f1`  
**Frozen Central Asset Storage code HEAD:** `f60b8d227433e89d60ea2a2ed57d8787295f2381`  
**Frozen HEAD message:** `fix(asset-storage): harden F4 document typing and session liveness`  
**Historical branch base:** `r7-i-legacy-authority-removal @ 333ca6695147a846651046daac0dcd6c9004b7ca`  
**Final R7 code baseline to integrate before F5:** `08f15a3768bff134889d399e65ad57ee987dbdca`  
**R7 final documentation commit:** `a20da9a9b7547b2c811fd77fa11cfb0464852703`  
**Checkpoint date:** 2026-09-22  
**Status:** **PAUSED intentionally after F4; F5 audited/frozen but NOT implemented; final R7 integration required before F5 resumes**

---

## 1. Purpose

This document freezes the exact Central Asset Storage state at code HEAD `f60b8d22` so the work can be resumed later without reconstructing the architecture from chat history.

It records:

- branch/base/commit state;
- completed F1→F4 work;
- final R7 compatibility invariants;
- the frozen F4→F5 audit;
- F5 P0/P1 blockers;
- the proposed F5 contract and implementation split;
- the required R7-J integration order before F5;
- the exact restart procedure for a future session.

No runtime code change is implied by this checkpoint.

A documentation-only commit may move the branch HEAD after this file is added. The frozen Central Asset Storage **code** baseline remains `f60b8d22` until implementation work resumes.

---

# 2. Frozen repository state

```text
branch:
feature/central-asset-storage-f1

frozen asset code HEAD:
f60b8d227433e89d60ea2a2ed57d8787295f2381

message:
fix(asset-storage): harden F4 document typing and session liveness

historical branch base:
r7-i-legacy-authority-removal
333ca6695147a846651046daac0dcd6c9004b7ca

final R7 runtime baseline:
r7-j-real-network-exit-gate
08f15a3768bff134889d399e65ad57ee987dbdca

R7 final exit-gate document:
docs/agent_execution_r7/R7_FINAL_EXIT_GATE.md
documentation commit:
a20da9a9b7547b2c811fd77fa11cfb0464852703
```

Relevant Central Asset Storage implementation chain:

```text
R7-I
333ca669
   │
   ├─ 8f74f45c  F1 persistence representation on R7-I
   ├─ 3039fcfe  F2 local object storage lifecycle
   ├─ b0a25560  F2-H R7 compatibility hardening
   ├─ dc135b5f  F2-H asset pin/delete race fencing
   ├─ 101f3cc5  F2-H stale STAGING reconciliation fencing
   ├─ ccf645c0  F3 streaming asset API
   ├─ fe6077b8  F3 upload/content hardening
   ├─ 8533ee60  F4 canonical message asset integration
   └─ f60b8d22  F4 document typing + session liveness hardening
```

Final R7 line from the same R7-I base:

```text
R7-I
333ca669
   │
   └─ R7-J line
      ├─ real reconnect/resume exit gate
      ├─ real server-restart WAITING recovery
      ├─ lost-ACK same-request replay
      ├─ concurrent ResumeClaim authority race
      └─ 08f15a37  final R7 runtime baseline
```

Current verified branch relationship:

```text
common base:
333ca669  R7-I

feature/central-asset-storage-f1:
+9 implementation commits from R7-I

r7-j-real-network-exit-gate:
+11 runtime/test commits from R7-I
+1 documentation-only R7 final exit-gate commit

post-R7-I unique file overlap:
NONE
```

This means the branches are diverged, but their post-R7-I change sets are structurally disjoint at this checkpoint.

---

# 3. Roadmap status

| Phase | State | Summary |
|---|---|---|
| F0 | DONE | Central Asset Storage contract freeze |
| F1 | CLOSED | `files`, `file_blobs`, `file_references`, `file_provider_bindings` |
| F2 | GREEN | ObjectStorage + LocalObjectStorageDriver + AssetService |
| F2-H | CLOSED | R7 pinning/delete/GC/reconciliation/MIME hardening |
| F3 | CLOSED | Canonical streaming `/v1/assets` |
| F4 | CLOSED | Canonical Message/Session asset integration |
| R7 sync | **PENDING BEFORE F5** | Integrate final R7-J baseline into this paused branch |
| F5 | **AUDITED / CONTRACT FROZEN / NOT IMPLEMENTED** | Provider asset hydration |
| F6 | NOT STARTED | Client/UI unified asset flow |
| F7 | NOT STARTED | Tool/provider-generated media ingestion |
| F8 | NOT STARTED | Legacy attachment cutover/backfill/cleanup |

Current intentional stop point:

```text
F4 complete
F5 audited only
NO F5 implementation yet

before F5:
integrate final R7-J baseline
run combined regression
re-freeze exact F5-0 blast radius
```

---

# 4. Core architecture frozen

```text
User
 owns
  ↓
FileAsset
  ├── FileBlob ───────────────→ canonical ObjectStorage
  ├── FileReference ──────────→ Message / Session / Project / AgentToolResult
  └── FileProviderBinding ────→ provider-specific cached representation
```

Canonical identity:

```text
asset_id
```

Canonical logical URI:

```text
asset://<asset_id>
```

Never canonical identity:

```text
local path
file:// URI
object-store path
signed URL
provider URI
provider file ID
base64
raw bytes
```

---

# 5. F1 — Persistence Representation

## 5.1 `file_blobs`

Physical canonical object authority.

Important states:

```text
STAGING
UNVERIFIED_LEGACY
READY
MISSING
DELETING
DELETED
ERROR
```

Important fields:

```text
storage_backend
bucket
object_key
size_bytes
sha256
declared_mime_type
detected_mime_type
etag
verified_at
deleted_at
metadata_json
```

## 5.2 `files`

Stable logical user-owned asset.

Important fields:

```text
id                  # asset_id
owner_user_id
organization_id
blob_id
filename
mime_type
origin_type
origin_id
state
revision
metadata_json
```

Asset states:

```text
STAGING
READY
QUARANTINED
DELETING
DELETED
ERROR
```

## 5.3 `file_references`

Current durable reference types:

```text
MESSAGE_CONTENT
SESSION_RESOURCE
PROJECT_RESOURCE
AGENT_TOOL_RESULT
```

Important invariant:

```text
Checkpoint is NOT an asset ownership/reference authority.
```

R7 checkpoint only snapshots stable logical `asset_id`.

## 5.4 `file_provider_bindings`

Represent provider-specific copies/cache entries of canonical Assistant assets.

F5 is the phase that activates this table as runtime authority.

---

# 6. F2 — Object Storage + AssetService

Current object storage abstraction supports:

```text
put_stream()
open_stream()
stat()
exists()
delete()
presign_get()
```

Current canonical implementation:

```text
LocalObjectStorageDriver
```

AssetService currently owns:

```text
STAGING → READY
READY → DELETING
DELETING → DELETED
```

and provides:

```text
streaming ingest
SHA-256 during ingest
server-side MIME detection/validation
owner authorization
streaming reads
Range-capable reads
logical deletion
physical collection
reconciliation
```

Object storage backend is configurable rather than hardcoded.

---

# 7. F2-H — R7 Compatibility Hardening

## 7.1 Tool result asset authority

Only:

```text
AgentToolResult.commit_state == COMMITTED
```

may create an:

```text
AGENT_TOOL_RESULT
```

asset reference.

PROVISIONAL results remain invisible.

```text
PROVISIONAL
    ✕ model context
    ✕ durable asset reference

COMMITTED
    ✓ reconstructed tool context
    ✓ durable asset reference
```

## 7.2 Reference vs DELETE race

Asset pinning uses revision fencing.

Conceptually:

```text
READY@N
  ↓ CAS
READY@(N+1)
  +
durable reference
```

Concurrent delete also uses revision/state fencing.

Only one side can win.

## 7.3 Logical delete separated from physical GC

```text
READY
  ↓
DELETING
  ↓
GC / reconciliation
  ↓
DELETED
```

Application/HTTP delete does not immediately destroy bytes.

## 7.4 Reconciliation

Handled cases include:

```text
READY + missing blob
STAGING + stale writer
DELETING + pending GC
ERROR + orphan object
```

The stale-STAGING race is fenced before object deletion.

---

# 8. F3 — Canonical `/v1/assets`

Implemented surface:

```text
POST   /v1/assets
GET    /v1/assets
GET    /v1/assets/{asset_id}
GET    /v1/assets/{asset_id}/content
GET    /v1/assets/{asset_id}/references
DELETE /v1/assets/{asset_id}
```

## 8.1 Upload

Canonical upload is chunk-streamed.

No whole-file `await file.read()` is required.

It enforces:

```text
owner scope
upload byte limit
chunk limit
MIME validation
canonical AssetService lifecycle
```

## 8.2 Content delivery

Supports:

```text
full streaming
single byte Range
206 Partial Content
416 invalid/unsupported Range
ETag
Accept-Ranges
nosniff
private/no-store
safe Content-Disposition
```

## 8.3 Delete

`DELETE /v1/assets/{id}` performs logical deletion only.

Physical deletion remains internal AssetService/GC/reconciliation work.

## 8.4 Legacy `/v1/files`

Still separate:

```text
/v1/assets
→ Assistant canonical user-owned assets

/v1/files
→ provider-specific legacy file proxy
```

Do not silently merge those semantics.

---

# 9. F4 — Canonical Message/Session Asset Integration

F4 is complete at this checkpoint.

## 9.1 GatewayAttachment identity

Canonical identity now uses:

```python
asset_id: Optional[str]
source: ... | "asset"
```

Canonical durable attachment:

```json
{
  "asset_id": "asset_123",
  "filename": "photo.png",
  "mime_type": "image/png",
  "size": 123456,
  "uri": "asset://asset_123",
  "source": "asset"
}
```

Legacy `id` is not implicitly converted into `asset_id`.

## 9.2 Canonical message content

New durable writes use:

```text
GatewayMessage.content
=
str | List[MessageContentPart]
```

New writes no longer create the legacy hidden wrapper:

```json
{
  "type": "text",
  "data": ...
}
```

Legacy wrapper remains read-compatible only.

## 9.3 Atomic Message + FileReference

Canonical message persistence now performs one SQL authority transaction:

```text
BEGIN

authorize session

validate content

extract asset occurrences

for every asset:
    verify owner
    verify FileAsset READY
    verify FileBlob READY
    canonicalize metadata from DB
    acquire revision fence

insert Message
allocate sequence

insert MESSAGE_CONTENT references

COMMIT
```

Failure rolls back together:

```text
Message
sequence
asset revision
FileReference
```

## 9.4 Client metadata is not authority

The client may provide `asset_id`, but canonical:

```text
filename
mime_type
size
sha256
asset:// URI
```

comes from the Asset Store.

## 9.5 Edit semantics

Message edit atomically:

```text
validates replacement content
pins replacement assets
removes old MESSAGE_CONTENT refs
updates content
inserts replacement refs
commits
```

## 9.6 Session deletion

Session deletion removes references/messages but not the user's FileAsset.

Session deletion is blocked while an active execution still depends on it.

## 9.7 ContextEngine

Context reconstruction:

- preserves structured multimodal content;
- unwraps only the exact legacy text envelope;
- dual-reads central assets plus legacy attachments;
- does not flatten canonical asset content.

## 9.8 R7 input-asset liveness

A `MESSAGE_CONTENT` asset in a session with an AgentExecution in:

```text
CREATED
RUNNING
WAITING
```

is considered live.

This prevents user-input assets from disappearing while a WAITING checkpoint still depends on them.

## 9.9 Provider fail-closed guard

F4 intentionally does NOT hydrate canonical assets.

If `asset://...` reaches provider execution without F5 hydration, execution must fail closed.

Never silently downgrade:

```text
asset image
→ text "asset://..."
```

---

# 10. Final R7 invariants that future phases must preserve

The original F2-H/F4 work was based on R7-I. R7 is now fully closed through R7-J, so F5+ must preserve the stronger final invariants below.

## R7-A — stable logical asset identity only

Checkpoint/ResumePlan may persist:

```text
asset_id
asset://asset_id
```

They must not persist:

```text
base64
provider URI
provider file ID
signed URL
local path
object store key
```

## R7-B — provider binding is not logical state

Provider bindings may:

```text
expire
be recreated
change provider URI
change provider file ID
be deleted
```

without changing:

```text
Message
checkpoint
ResumePlan logical meaning
asset_id
```

## R7-C — PROVISIONAL remains invisible

F5 must not create a path where a PROVISIONAL tool result becomes visible just because the provider accepted/uploaded its file.

## R7-D — deterministic reconstruction

R7 reconstruction must retain:

```text
same logical transcript
same asset IDs
same canonical parallel tool ordering
```

Hydration happens only after logical reconstruction.

## R7-E/F — same logical invocation and single resume authority

Provider hydration must not create another logical tool invocation or alter the durable ResumeClaim decision.

For resumed work:

```text
execution_id       = SAME
invocation_id      = SAME
tool_call_id       = SAME
asset_id           = SAME
provider binding   = MAY CHANGE
connection_id      = MAY CHANGE
provider file id   = MAY CHANGE
```

## R7-G — ACK is not authority

Provider hydration may occur only inside a runtime path that has already obtained canonical R7 authority.

A wire ACK, provider upload, provider URI, or provider-side file object must never become a second execution authority.

## R7-H — stable ticket/request correlation

Hydration must not mutate or replace:

```text
resume_request_id
checkpoint identity
PendingResumeTicket logical identity
```

A reconnect generation may rehydrate provider state, but must not create a new logical execution/ticket merely because transient provider state expired.

## R7-I — legacy continuation remains read-only compatibility

F5 must not reintroduce old continuation JSON/branch-merge state as a place to persist provider asset identity.

## R7-J — process restart remains safe

Final R7 proves WAITING resume across real WebSocket generations and server restart.

Therefore F5 provider hydration must remain reconstructable from:

```text
canonical durable Message
asset_id
FileAsset/FileBlob
current R7 execution/checkpoint state
```

and not require process-local provider file objects to resume correctly.

---

# 11. Current pause point — F5 Provider Asset Hydration

F5 has been audited and contract-frozen.

F5 has NOT been implemented.

The provider fail-closed guard from F4 is intentionally still active.

Do not remove it until complete F5 hydration exists.

Before any F5 code is written, final R7-J must be integrated into this feature branch and the exact F5 blast radius must be re-audited on that combined HEAD.

---

# 12. F5 placement decision

Hydration must happen:

```text
after routing chooses a concrete provider
before ProviderExecutor dispatches that provider request
```

Target flow:

```text
Canonical InferenceRequest
        │
        ▼
ChatExecutionHandler
        │
        ├── choose provider A
        │
        ▼
AssetHydrationService
        │
        ├── trusted owner authorization
        ├── short-lived asset read lease
        ├── FileAsset/FileBlob READY validation
        ├── INLINE transform
        │      or
        └── PROVIDER_FILE binding
        │
        ▼
provider-attempt transient body
        │
        ▼
ProviderExecutor.execute(provider=A)
```

If provider A fails:

```text
canonical body
      ↓
hydrate independently for provider B
```

Provider A's transformed body must never be reused by provider B.

---

# 13. F5 P0 blockers

## P0-1 — Trusted principal propagation

Asset authorization must not use client-controlled request metadata.

F5 needs trusted server-side execution context such as:

```text
owner_user_id
session_id
```

sourced from:

```text
DIRECT → authenticated Identity
AGENT  → AgentExecutionContext.identity
```

These fields must not be serialized into provider request JSON.

## P0-2 — Binding reservation impossible with current NOT NULL provider ID

Current `provider_file_id` requires a value too early.

Reliable external side-effect protocol needs:

```text
SQL reserve PROCESSING
↓
external provider upload
↓
SQL finalize ACTIVE
```

Therefore F5 needs a schema amendment.

## P0-3 — No unique binding slot

Need one logical slot:

```text
(file_id, provider_name, provider_namespace)
```

Otherwise concurrent inference can create duplicate provider copies.

## P0-4 — No binding CAS/lease authority

Binding has `revision` but current runtime does not use it as lifecycle authority.

F5 needs:

```text
reserve_provider_binding()
claim_stale_provider_binding()
activate_provider_binding()
expire_provider_binding()
mark_provider_binding_error()
```

with state + revision + lease fencing.

## P0-5 — Binding expiry ignored

Current active binding lookup only checks:

```text
state == ACTIVE
```

It must also reject expired entries.

## P0-6 — Provider lifecycle metadata incomplete

Gemini binding needs provider lifecycle fields such as:

```text
expirationTime
state
```

when available.

## P0-7 — No true async ObjectStorage → provider upload

Canonical object reads return async byte streams.

Current Gemini Files wrapper expects `UploadFile/BinaryIO`.

F5 must add a provider file upload contract based on:

```text
AsyncIterable[bytes]
```

Large assets must never be buffered completely into `BytesIO`.

## P0-8 — Missing transient hydration read lease

DIRECT inference has no AgentExecution liveness pin.

Hydration can race:

```text
read blob
vs
DELETE / GC
```

F5 needs a durable short-TTL asset hydration/read lease.

## P0-9 — Provider copy cleanup missing

After F5 an Assistant asset may have:

```text
canonical blob
+
Gemini copy
+
other provider copies
```

Logical delete must deny new hydration and eventually remove provider copies.

## P0-10 — Canonical message shape vs provider converters

F4 canonical parts use:

```text
part["data"]
```

provider converters still contain legacy assumptions.

F5 must explicitly canonicalize provider conversion.

---

# 14. Provider capability inconsistency discovered

Current provider capability metadata is not safe as sole File API authority.

Observed:

```text
Gemini:
    self.files exists
    ProviderCapability.FILES missing

OpenAI:
    ProviderCapability.FILES declared
    self.files implementation missing
```

Required invariant for F5:

```text
ProviderCapability.FILES
⇔
provider.files implements canonical FileProvider
```

Architecture tests should enforce this.

---

# 15. F5 provider binding state machine

Proposed:

```text
none
 ↓
PROCESSING
 ↓
ACTIVE
 ↓
EXPIRED
 ↓
PROCESSING
 ↓
ACTIVE
```

Failure:

```text
PROCESSING
 ↓
ERROR
```

Delete:

```text
ACTIVE / EXPIRED / ERROR
 ↓
DELETING
 ↓
DELETED
```

Required schema amendments:

```text
provider_file_id      nullable while PROCESSING
provider_uri          nullable
lease_token           nullable
lease_expires_at      nullable
revision              CAS authority
```

Constraints:

```text
ACTIVE
→ provider_file_id IS NOT NULL

PROCESSING
→ lease_token IS NOT NULL
→ lease_expires_at IS NOT NULL
```

Unique binding slot:

```text
UNIQUE(
    file_id,
    provider_name,
    provider_namespace
)
```

---

# 16. Proposed hydration/read lease

Recommended new durable table:

```text
file_asset_leases
```

Conceptual fields:

```text
id
file_id
lease_type = PROVIDER_HYDRATION
owner_user_id
session_id
execution_id
request_id
provider_name
lease_token
expires_at
created_at
```

Target lifecycle:

```text
authorize READY asset
↓
create short-lived lease
↓
commit
↓
read/upload/hydrate
↓
release
```

DELETE/GC must respect unexpired hydration leases.

Crash recovery is handled through TTL expiry.

---

# 17. Provider representation strategy

Each provider should choose:

```text
INLINE
PROVIDER_FILE
UNSUPPORTED
```

Examples:

```text
small inline-supported image
→ INLINE

large/reused file with provider Files API
→ PROVIDER_FILE

unsupported modality/provider
→ UNSUPPORTED
```

Do not use one global inline-size rule for all providers.

Policy belongs to provider strategy/config.

---

# 18. Retry and fallback semantics

Hydration happens once per provider attempt, outside provider chat retry.

Correct:

```text
hydrate Gemini
↓
Gemini chat attempt
↓ retry
Gemini chat retry
```

No repeated asset upload during chat retries.

Fallback:

```text
Gemini fails completely
↓
start again from immutable canonical body
↓
hydrate fallback provider independently
```

External asset upload should have its own bounded retry/reconciliation policy.

---

# 19. Unknown external upload outcome

SQL + external Files API cannot provide true distributed ACID.

Possible:

```text
provider upload succeeds
process dies before ACTIVE binding commit
```

An external orphan may remain.

Accepted architecture:

```text
Assistant canonical asset remains exactly one logical asset

provider external upload may be at-least-once

external orphan copies are later reconciled/cleaned
```

Provider copy is never canonical authority.

---

# 20. ProviderFileDescriptor should be introduced

F5 should stop using `GatewayAttachment` as provider Files response DTO.

Recommended split:

```text
GatewayAttachment
= Assistant canonical/message DTO

ProviderFileDescriptor
= provider-specific temporary/cache DTO
```

Suggested fields:

```text
provider_name
provider_file_id
uri
mime_type
size_bytes
state
expires_at
checksum
metadata
```

---

# 21. F5 frozen invariants

```text
ASSET-F5-I01
Canonical message/checkpoint never changes during hydration.

ASSET-F5-I02
Hydration receives trusted authenticated owner context,
never owner identity from client metadata.

ASSET-F5-I03
Hydration occurs after provider selection and before provider dispatch.

ASSET-F5-I04
Every provider attempt gets a new transformed body derived
from the immutable canonical body.

ASSET-F5-I05
Provider chat retries reuse one provider hydration result.

ASSET-F5-I06
Provider fallback hydrates independently for the fallback provider.

ASSET-F5-I07
Provider binding is a cache, never canonical asset identity.

ASSET-F5-I08
Only READY FileAsset + READY FileBlob can hydrate.

ASSET-F5-I09
Hydration holds a durable, time-bounded asset read lease.

ASSET-F5-I10
DELETE/GC cannot destroy bytes under an active hydration lease.

ASSET-F5-I11
One asset/provider/namespace has one binding slot.

ASSET-F5-I12
PROCESSING binding is acquired through revision/CAS + lease.

ASSET-F5-I13
ACTIVE binding must contain provider file identity.

ASSET-F5-I14
Expired provider binding is never reused.

ASSET-F5-I15
Provider-reported expiry is authoritative when available.

ASSET-F5-I16
Large provider upload streams from ObjectStorage without full buffering.

ASSET-F5-I17
Inline hydration is bounded by provider-specific policy.

ASSET-F5-I18
Unsupported modality/provider fails/skips cleanly before provider chat call.

ASSET-F5-I19
Provider URI/provider_file_id/base64 never enters durable transcript.

ASSET-F5-I20
Binding changes never mutate checkpoint or ResumePlan fingerprint.

ASSET-F5-I21
Asset logical deletion schedules provider-copy cleanup.

ASSET-F5-I22
Crash-stale hydration/binding leases are reclaimable.

ASSET-F5-I23
Unknown provider upload outcome may create external orphans,
but never duplicate canonical Assistant assets.

ASSET-F5-I24
Provider-generated output ingestion remains F7.

ASSET-F5-I25
Provider hydration must preserve final R7 same-execution /
same-invocation resume identity across reconnect and restart.

ASSET-F5-I26
A provider binding/upload is transient cache state and can never
become ResumeClaim, PendingResumeTicket, checkpoint, or ACK authority.
```

---

# 22. Recommended F5 implementation split when resumed

## F5-0 — Persistence/lease representation

Implement only:

```text
file_provider_bindings schema amendments
file_asset_leases
binding CAS repositories
lease CAS repositories
migration tests
```

No provider call yet.

## F5-1 — Trusted inference execution context

Propagate:

```text
owner_user_id
session_id
```

through DIRECT + AGENT inference.

Do not serialize those fields to provider request JSON.

## F5-2 — Generic AssetHydrationService

Implement:

```text
immutable canonical transforms
owner authorization
READY validation
read lease acquisition/release
provider strategy interface
```

## F5-3 — Async FileProvider stream contract

Introduce:

```text
AsyncIterable[bytes]
```

provider upload API.

Implement Mock provider first.

## F5-4 — Gemini provider binding

Implement:

```text
stream upload
PROCESSING reservation
ACTIVE finalize
provider expiry/state parsing
binding reuse
expired binding recreation
```

## F5-5 — Inline strategies and converter canonicalization

Update provider adapters/converters to consume canonical MessageContentPart shape.

## F5-6 — Fallback/retry/delete/reconciliation integration

Implement:

```text
per-provider-attempt hydration
fallback isolation
provider-copy cleanup
stale lease recovery
external orphan reconciliation
```

## F5-7 — Exit gate

Run full concurrency/restart/fallback/final-R7 matrix.

---

# 23. Mandatory F5 regression matrix

F5 must not close until tests prove:

```text
same asset + same provider + 20 concurrent inference
→ one binding slot

second inference
→ reuses ACTIVE binding

expired binding
→ recreated
→ checkpoint unchanged

provider A fails after hydration
→ provider B starts from canonical body

provider chat retry
→ does not upload asset again

DIRECT hydration + concurrent DELETE
→ GC cannot delete leased bytes

Agent WAITING restart
→ same asset_id
→ same execution_id
→ same logical invocation_id
→ provider binding may be rebuilt
→ no tool rerun
→ no checkpoint rewrite

lost execution.resume.accepted ACK
→ retry keeps same resume_request_id
→ hydration/cache changes do not mint second execution authority

concurrent resume authority race
→ one R7 ResumeClaim winner
→ asset/provider hydration cannot create a second winner

foreign asset_id
→ denied before object/provider I/O

large asset
→ ObjectStorage async chunks → provider
→ no whole-file BytesIO

small inline asset
→ bounded memory

unsupported modality
→ clean provider skip/failure
→ no false circuit-breaker poisoning

provider upload succeeds + SQL finalize fails
→ recoverable/reconcilable binding state

user DELETE
→ new hydration denied
→ provider copies scheduled for deletion
→ canonical GC respects leases/references

no provider_file_id
no provider URI
no base64
inside:
    Message
    AgentExecution checkpoint
    ResumePlan
    ResumeClaim
    PendingResumeTicket
```

---

# 24. Explicit F5 non-goals

Do NOT combine F5 with:

```text
F6 client/UI asset upload migration
F7 provider-generated media ingestion
F8 legacy attachment removal/backfill
R7 checkpoint redesign
R7 ResumeClaim redesign
R7 PendingResumeTicket redesign
Message persistence redesign
Tool-result commitment redesign
```

F5 is provider hydration only.

---

# 25. Likely F5 blast radius

Expected areas:

```text
se/src/infrastructure/storage/models/sql/assets/
se/src/infrastructure/storage/repositories/assets.py
se/src/infrastructure/storage/migrations/sql/versions/

se/src/application/assets/

se/src/runtimes/agent/contracts/inference.py
se/src/runtimes/agent/adapters/inference.py
se/src/runtimes/chat/direct.py
se/src/runtimes/agent/runtime.py

se/src/provider/handlers/chat_handler.py
se/src/provider/core/interfaces/file.py
se/src/provider/gemini/api/files.py
se/src/provider/gemini/converters/
se/src/provider/ollama/converters/
se/src/provider/mock/

se/src/infrastructure/config/schemas.py
se/config/default.yaml

architecture/integration/provider tests
```

Surfaces that should remain logically stable unless a new blocker is proven:

```text
R7 checkpoint persistence
ResumePlan logical representation
ResumeClaim authority semantics
PendingResumeTicket request identity
R7-J real-network reconnect/restart semantics
F4 canonical Message persistence
/v1/assets public semantics
legacy /v1/files semantics
```

---

# 26. Required branch integration order before F5 resumes

At the time of this checkpoint:

```text
main:
24f3c5fc808729cbe714e13650c37262bccfef48

r7-j-real-network-exit-gate:
runtime baseline 08f15a37
documentation head a20da9a9

feature/central-asset-storage-f1:
asset code baseline f60b8d22
```

Required order:

```text
1. Merge r7-j-real-network-exit-gate into main.
2. Verify full CI on integrated main.
3. Update feature/central-asset-storage-f1 from that final-R7 main
   (merge main into feature branch or rebase the 9 asset commits onto final R7).
4. Run combined final-R7 + asset regressions.
5. Re-audit exact F5 blast radius on the combined HEAD.
6. Re-freeze F5-0.
7. Only then begin F5 implementation.
8. After F5/F6/... feature work reaches its own exit gate,
   merge the asset feature branch back to main.
```

Why R7 must merge first:

```text
R7 is the execution/resume authority baseline.

Central Asset Storage is a downstream feature that already depends on R7
tool-result commitment, WAITING liveness, deterministic reconstruction,
and same-execution resume semantics.

Merging asset work first would force main to carry an R7-I-based feature
while the canonical execution authority is still moving through R7-J.
```

Current structural conflict assessment:

```text
common base:
R7-I @ 333ca669

R7-J unique files:
docs/agent_execution_r7/R7_FINAL_EXIT_GATE.md
se/src/transport/gateway/api/v1/events_router.py
se/tests/e2e/test_r7_j_real_network_exit_gate.py

Central Asset Storage post-R7-I unique files:
asset/message/storage/provider/session surfaces

exact unique-file overlap:
NONE
```

Therefore no textual conflict is expected from the current post-R7-I deltas, but the combined branch must still run semantic regression because F4 and future F5 depend on R7 lifecycle behavior.

Preferred history-preserving integration for the paused feature branch:

```text
merge final-R7 main into feature/central-asset-storage-f1
```

A rebase is also valid if clean linear history is explicitly preferred, but it rewrites the 9 feature commit SHAs. Do not rewrite the frozen architectural meaning of this checkpoint.

---

# 27. Resume procedure for a future session

When this work resumes:

```text
1. Read this checkpoint.
2. Query current main, r7-j-real-network-exit-gate,
   and feature/central-asset-storage-f1 HEADs.
3. Confirm whether final R7 has already landed in main.
4. Compare current asset branch with frozen asset code HEAD f60b8d22.
5. Audit all commits added after this checkpoint.
6. If final R7 is not yet in the asset branch, integrate it first.
7. Run:
      - full repository CI
      - R7-J real-network exit gate
      - F1/F2/F2-H/F3/F4 asset regressions
8. Re-check exact F5 blast radius on the combined HEAD.
9. Re-freeze F5-0 schema/migration contract.
10. Implement F5-0 only.
11. Run targeted tests and full CI.
12. Continue F5-1 → F5-7 sequentially.
```

If HEAD changes, this document remains the architectural baseline, not proof that every old call site is still exact.

---

# 28. Suggested future resume prompt

```text
Kiểm tra HEAD hiện tại của main và feature/central-asset-storage-f1.

Đọc lại:
- docs/agent_execution_r7/R7_FINAL_EXIT_GATE.md
- docs/CENTRAL_ASSET_STORAGE_HEAD_CHECKPOINT_F60B8D22.md

Xác nhận final R7 baseline 08f15a37 đã được tích hợp vào asset branch.
So sánh asset branch với frozen code checkpoint
f60b8d227433e89d60ea2a2ed57d8787295f2381.

Audit mọi thay đổi sau checkpoint, chạy combined R7-J + F1→F4 regressions,
rồi re-freeze F5-0 exact schema cho
file_provider_bindings + file_asset_leases + CAS/lease semantics.

Chưa triển khai provider hydration cho tới khi combined HEAD xanh.
Chưa làm F6/F7/F8.
```

Alternative:

```text
Audit lại F5 boundary trên HEAD đã chứa final R7,
xác nhận trusted inference principal,
per-provider-attempt hydration placement,
binding CAS/expiry,
hydration read lease,
async ObjectStorage→provider streaming,
provider-copy delete lifecycle,
và invariant same execution / same invocation qua R7 reconnect/restart.

Chưa code cho tới khi F5-0 được freeze lại.
```

---

# 29. Final frozen status

```text
Repository:
boxs-51/assistant

Branch:
feature/central-asset-storage-f1

Frozen Central Asset Storage code HEAD:
f60b8d227433e89d60ea2a2ed57d8787295f2381

Historical branch base:
R7-I
333ca6695147a846651046daac0dcd6c9004b7ca

Final R7 runtime baseline required before F5:
08f15a3768bff134889d399e65ad57ee987dbdca

R7 final documentation commit:
a20da9a9b7547b2c811fd77fa11cfb0464852703

F1:
CLOSED

F2:
GREEN

F2-H:
CLOSED

F3:
CLOSED

F4:
CLOSED / CI GREEN AT FROZEN ASSET HEAD

FINAL R7 SYNC:
PENDING ON THIS PAUSED BRANCH
MUST COMPLETE BEFORE F5

F5:
AUDITED
CONTRACT FROZEN
NOT IMPLEMENTED

F6:
NOT STARTED

F7:
NOT STARTED

F8:
NOT STARTED

ACTION:
PAUSE CENTRAL ASSET STORAGE WORK HERE
INTEGRATE FINAL R7 BEFORE ANY F5 IMPLEMENTATION
```

---

## Single most important rule when work resumes

```text
Durable state owns asset_id.

Final R7 owns execution/resume authority.

Provider hydration is transient.

Provider-specific file identity must never become canonical Message,
checkpoint, ResumePlan, ResumeClaim, PendingResumeTicket,
or tool-result identity.
```
