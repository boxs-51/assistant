# DEFERRED IMPLEMENTATION NOTICE — ROADMAP R8→R14+ TAKES PRIORITY

> **F5-0 remains contract-frozen but implementation is now intentionally PAUSED.**
>
> Do not implement F5-0/F5-1+ until the Agent Execution / Continuation / Branching roadmap
> `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md` has completed the remaining
> R8→R14+ implementation and exit gates.
>
> The F5-0 persistence contract below remains the frozen resumption baseline. It must be
> re-audited against the final post-R14+ repository HEAD before any F5 code is written.
>
> Priority workstream:
>
> ```text
> R8 → R9 → R10 → R11 → R12 → R13 → R14 → post-R14+ gates
> ```
>
> Central Asset Storage remains parked at completed F4 + audited/frozen F5-0.

---

# CENTRAL ASSET STORAGE — F5-0 CONTRACT FREEZE

**Repository:** `boxs-51/assistant`  
**Branch:** `feature/central-asset-storage-f1`  
**Combined code baseline:** `4b99f679e1ea7026215939901a47858b9cc9aae3`  
**Final R7 main parent:** `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`  
**Historical F4 asset code baseline:** `f60b8d227433e89d60ea2a2ed57d8787295f2381`  
**Freeze date:** 2026-09-22  
**Status:** **F5-0 CONTRACT FROZEN / NOT IMPLEMENTED**

> This document is a persistence/lease contract freeze only. It does not implement provider hydration, provider upload, F5-1+, F6, F7, or F8.

---

## 1. Why this re-freeze exists

The Central Asset Storage F1→F4 line was originally paused on top of R7-I.

Final R7 has now been integrated into the asset branch through the merge commit:

```text
4b99f679e1ea7026215939901a47858b9cc9aae3
```

The two parents are:

```text
feature/central-asset-storage-f1 pause/document line
93b45b0986c0c80986079490aa84f667ea4323ab

final R7 main + consolidated Phase 5 CI contract
a55e4fd2a20ddccbd227e770d26fae72e33bc88e
```

The post-R7-I unique file sets had zero overlap before the merge. The merge therefore required no textual conflict resolution.

F5-0 is re-frozen only after validating the combined R7 + F1→F4 state.

---

## 2. Combined exit-gate evidence

Exact combined code HEAD:

```text
4b99f679e1ea7026215939901a47858b9cc9aae3
```

### Phase 5 consolidated gate

```text
40 passed in 1.59s
SUCCESS
```

The six historical Phase 5 workflows have been replaced by one workflow:

```text
.github/workflows/phase5-exit-gates.yml
```

The Phase 5 architecture tests now reference the consolidated workflow, while the repository-wide full suite remains owned by:

```text
.github/workflows/architecture-baseline.yml
```

### Architecture Baseline

Linux:

```text
830 passed
1 skipped
21 warnings
SUCCESS
```

Windows client contracts:

```text
68 passed
SUCCESS
```

The Linux command is the repository-wide:

```text
python -m pytest -q
```

so the combined proof includes:

- R7-J real-network reconnect/restart/lost-ACK/concurrent-resume tests;
- R7 checkpoint/reconstruction/ResumeClaim/PendingResumeTicket regressions;
- Central Asset Storage F1/F2/F2-H/F3/F4 architecture tests;
- Central Asset Storage migration/storage tests;
- legacy Phase 5 exit gates through the consolidated workflow contract.

No P0/P1 regression was found at the R7-J ↔ F1-F4 boundary.

---

## 3. Semantic conflict audit — R7-J ↔ F1-F4

### 3.1 Exact file conflict

From common base R7-I:

```text
R7-J/final-main unique files
∩
Central Asset Storage F1-F4 unique files
=
empty set
```

No production file required manual merge conflict resolution.

### 3.2 R7-J transport ownership vs asset lifecycle

R7-J changes `events_router.py` so resume planning executes in detached request tasks while the WebSocket receive loop remains available for R6 reconciliation.

Disconnect ordering is:

```text
disconnect_connection(active_connection_id)
→ fail connection-bound invocation/reconciliation futures
→ cancel/drain detached resume tasks
→ disconnect websocket
```

F1-F4 asset lifecycle does not own or mutate this transport authority.

**Frozen conclusion:** no second asset lifecycle authority is introduced by R7-J.

### 3.3 R7 WAITING liveness vs session deletion

F4 session deletion rejects deletion while an AgentExecution exists in:

```text
CREATED
RUNNING
WAITING
```

R7-J reconnect/recovery preserves canonical durable `WAITING` as the resumable execution state.

Therefore an input asset referenced by session messages remains protected while the R7 execution can still resume.

### 3.4 Checkpoint reconstruction vs canonical asset identity

F2-H already proves checkpoint JSON round-trip preserves:

```text
asset_id
asset://<asset_id>
```

Provider identity does not enter checkpoint state.

R7-J restart reconstructs from durable state and therefore consumes the same stable logical asset identity.

### 3.5 Structured multimodal messages vs R7 context reconstruction

F4 canonical message content is:

```text
str | List[MessageContentPart]
```

`ContextEngine` returns structured content without flattening it.

`gateway_message_to_inference()` uses JSON-safe conversion and passes the complete content into `InferenceMessage.content`.

R7 context assembly also validates `prior_messages` back into `InferenceMessage`.

**Frozen conclusion:** final R7 reconstruction does not require converting canonical asset parts to strings.

### 3.6 Provider execution boundary

F4 intentionally rejects an unhydrated canonical asset before provider execution:

```text
asset://...
→ ProviderError
→ F5 must hydrate first
```

This fail-closed guard remains present on the combined HEAD.

R7 restart/reconnect therefore cannot silently downgrade a canonical asset reference into provider text.

---

## 4. Migration ordering after final R7 integration

The combined Alembic chain is:

```text
...
12a_r6_remote_reconciliation
  ↓
13a_r7_durable_resume
  ↓
13b_r7_pending_snapshot
  ↓
f1a_central_asset_storage
  ↓
f2h_r7_asset_compat
```

F1 is explicitly based on:

```text
down_revision = "13b_r7_pending_snapshot"
```

F2-H is explicitly based on:

```text
down_revision = "f1a_central_asset_storage"
```

There is no final-R7 migration after `13b`.

Therefore F5-0 must append one new revision to the existing single head.

---

# 5. F5-0 exact scope

F5-0 is **persistence and lease representation only**.

Allowed implementation surfaces:

```text
se/src/infrastructure/storage/models/sql/assets/
se/src/infrastructure/storage/repositories/assets.py
se/src/infrastructure/storage/migrations/sql/versions/
se/src/infrastructure/storage/models/sql/assets/__init__.py
migration/repository tests
```

`SqlAlchemyUnitOfWork.assets` already exists and remains the repository access point.

F5-0 must not add provider network calls.

F5-0 must not remove the F4 provider fail-closed guard.

F5-0 must not change R7 checkpoint, ResumePlan, ResumeClaim, PendingResumeTicket, or execution lifecycle contracts.

---

# 6. F5-0 blockers confirmed on combined HEAD

## P0-1 — provider_file_id is NOT NULL too early

Current:

```text
file_provider_bindings.provider_file_id
nullable = false
```

But safe external upload requires:

```text
reserve SQL slot
→ external provider upload
→ finalize provider identity
```

The reservation therefore must exist before `provider_file_id` is known.

## P0-2 — unique provider binding slot is missing

Current uniqueness protects external provider identity:

```text
(provider_name, provider_namespace, provider_file_id)
```

It does not enforce one logical Assistant binding slot for:

```text
(file_id, provider_name, provider_namespace)
```

Concurrent hydration can therefore allocate multiple rows for the same logical slot.

## P0-3 — binding lease fencing is missing

`revision` exists, but provider binding lifecycle has no canonical CAS/lease methods.

Current repository only exposes:

```text
create_provider_binding()
get_active_provider_binding()
```

## P0-4 — ACTIVE lookup ignores expiry

Current lookup checks:

```text
state == ACTIVE
```

It does not reject:

```text
expires_at <= now
```

## P0-5 — no hydration read lease representation

No durable `file_asset_leases` table exists.

Once F5 starts reading canonical bytes for DIRECT inference, delete/GC must be able to observe a bounded read lease.

---

# 7. Frozen F5-0 migration

New migration:

```text
revision:
f5a_provider_hydration_leases

down_revision:
f2h_r7_asset_compat
```

No branch migration is allowed.

Target chain:

```text
13b_r7_pending_snapshot
→ f1a_central_asset_storage
→ f2h_r7_asset_compat
→ f5a_provider_hydration_leases
```

---

# 8. file_provider_bindings schema amendment

Keep existing fields and state vocabulary.

Add:

```text
lease_token       VARCHAR(255) NULL
lease_expires_at  TIMESTAMP WITH TIME ZONE NULL
```

Change:

```text
provider_file_id
NOT NULL
→ NULLABLE
```

Keep:

```text
provider_uri nullable
revision nonnegative
expires_at nullable
last_verified_at nullable
metadata_json nullable
```

---

## 8.1 Logical slot uniqueness

Add:

```text
UNIQUE(
    file_id,
    provider_name,
    provider_namespace
)
```

Canonical constraint name:

```text
uq_file_provider_bindings_slot
```

Keep the existing external-provider identity constraint:

```text
UNIQUE(
    provider_name,
    provider_namespace,
    provider_file_id
)
```

This external identity constraint may contain NULL `provider_file_id` while a row is PROCESSING.

The logical slot constraint is the concurrency authority.

---

## 8.2 Binding state checks

Add:

```text
ACTIVE
→ provider_file_id IS NOT NULL
```

Canonical check:

```text
ck_file_provider_bindings_active_identity
```

Add:

```text
PROCESSING
→ lease_token IS NOT NULL
→ lease_expires_at IS NOT NULL
```

Canonical check:

```text
ck_file_provider_bindings_processing_lease
```

Add:

```text
state != PROCESSING
→ lease_token IS NULL
→ lease_expires_at IS NULL
```

Canonical check:

```text
ck_file_provider_bindings_lease_scope
```

The existing state constraint remains:

```text
PROCESSING
ACTIVE
EXPIRED
DELETING
DELETED
ERROR
```

---

# 9. Migration normalization rules

F5 has not yet activated provider-binding reservations.

Therefore any pre-F5 row in:

```text
state == PROCESSING
```

has no valid F5 lease authority.

During the F5-0 migration, before the new PROCESSING lease checks are installed:

```text
PROCESSING
→ ERROR
revision = revision + 1
```

No external provider object is deleted by this migration.

Existing `ACTIVE`, `EXPIRED`, `ERROR`, `DELETING`, and `DELETED` rows retain their provider identity and metadata.

Before adding `uq_file_provider_bindings_slot`, the migration must preflight duplicate:

```text
(file_id, provider_name, provider_namespace)
```

groups.

If duplicates exist, migration must fail closed with explicit diagnostics.

It must not silently choose or delete one provider copy.

---

# 10. New durable file_asset_leases table

Create:

```text
file_asset_leases
```

Exact fields:

```text
id                VARCHAR(255) PRIMARY KEY
file_id           VARCHAR(255) NOT NULL
lease_type        VARCHAR(64)  NOT NULL
owner_user_id     VARCHAR(255) NOT NULL
session_id        VARCHAR(255) NULL
execution_id      VARCHAR(255) NULL
request_id        VARCHAR(255) NOT NULL
provider_name     VARCHAR(64)  NOT NULL
lease_token       VARCHAR(255) NOT NULL
expires_at        TIMESTAMP WITH TIME ZONE NOT NULL
created_at        TIMESTAMP WITH TIME ZONE NOT NULL
```

F5-0 supports one lease type:

```text
PROVIDER_HYDRATION
```

Add check:

```text
lease_type IN ('PROVIDER_HYDRATION')
```

---

## 10.1 Lease identity and foreign keys

```text
file_id
→ files.id
→ ON DELETE RESTRICT
```

An active/read lease must not disappear because a FileAsset row is hard-deleted.

```text
owner_user_id
→ users.id
→ ON DELETE RESTRICT
```

Optional context links:

```text
session_id
→ sessions.id
→ ON DELETE SET NULL

execution_id
→ agent_executions.id
→ ON DELETE SET NULL
```

Session/execution cleanup must not destroy the byte-read lease itself.

Add:

```text
UNIQUE(lease_token)
```

Canonical name:

```text
uq_file_asset_leases_token
```

---

## 10.2 Lease indexes

Required indexes:

```text
ix_file_asset_leases_file_expiry
(file_id, expires_at)

ix_file_asset_leases_expiry
(expires_at)

ix_file_asset_leases_request
(request_id)

ix_file_asset_leases_execution
(execution_id)
```

An active hydration lease is defined by:

```text
matching file_id
AND expires_at > now
```

Release physically deletes the row by the exact `lease_token`.

A crashed owner leaves the row until TTL expiry.

The token is the release fence.

No mutable lease state machine is required in F5-0.

---

# 11. Frozen provider-binding repository contract

The following repository semantics are mandatory.

## 11.1 reserve_provider_binding()

Inputs include:

```text
file_id
provider_name
provider_namespace
lease_token
lease_expires_at
```

Behavior:

```text
no slot
→ create PROCESSING@revision0

valid ACTIVE slot
→ return/reuse ACTIVE

live PROCESSING slot owned by another unexpired lease
→ report contention
→ no mutation

stale PROCESSING slot
→ reclaim only through revision + old-state fence

EXPIRED / ERROR slot
→ transition to PROCESSING through revision fence
```

The logical unique slot prevents two rows from winning concurrently.

## 11.2 claim_stale_provider_binding()

Must CAS on at least:

```text
binding_id
expected_revision
state == PROCESSING
old lease_expires_at <= now
```

On success:

```text
new lease_token
new lease_expires_at
revision + 1
```

## 11.3 activate_provider_binding()

Must CAS on:

```text
binding_id
expected_revision
state == PROCESSING
lease_token == caller lease
```

Success writes:

```text
provider_file_id
provider_uri
expires_at
last_verified_at
metadata_json
state = ACTIVE
lease_token = NULL
lease_expires_at = NULL
revision + 1
```

## 11.4 expire_provider_binding()

CAS:

```text
ACTIVE@expected_revision
→ EXPIRED@(revision+1)
```

No canonical Message/checkpoint state changes.

## 11.5 mark_provider_binding_error()

A PROCESSING owner may fail its reservation only with the exact lease token.

```text
PROCESSING@expected_revision
+ matching lease_token
→ ERROR@(revision+1)
→ clear binding lease
```

## 11.6 get_active_provider_binding()

A reusable binding must satisfy:

```text
state == ACTIVE
AND provider_file_id IS NOT NULL
AND (
    expires_at IS NULL
    OR expires_at > now
)
```

Expired rows are not reusable even if their persisted state has not yet been changed to EXPIRED.

---

# 12. Frozen hydration-read lease repository contract

Required primitives:

```text
create_hydration_lease()
release_hydration_lease()
has_active_hydration_lease()
delete_expired_hydration_leases()
```

## create_hydration_lease()

Must require a caller-generated cryptographically unpredictable `lease_token`.

It persists trusted server-side context only.

No owner identity may come from client-controlled metadata.

## release_hydration_lease()

Delete only:

```text
file_id == expected file
AND lease_token == exact caller token
```

A foreign/stale token is a no-op/fail-closed outcome.

## has_active_hydration_lease()

True only when:

```text
file_id matches
AND expires_at > now
```

F5-2/F5-6 will later make canonical read/GC lifecycle consume this primitive.

## delete_expired_hydration_leases()

May remove only:

```text
expires_at <= now
```

No live lease may be reclaimed by age heuristics other than its explicit expiry timestamp.

---

# 13. Revision and authority invariants

### F5-0-I01

`FileAsset.revision` and `FileProviderBinding.revision` are different authorities.

Asset revision fences canonical asset lifecycle.

Binding revision fences provider-cache lifecycle.

### F5-0-I02

A provider binding never changes:

```text
asset_id
Message
checkpoint
ResumePlan
ResumeClaim
PendingResumeTicket
```

### F5-0-I03

A hydration read lease never becomes a FileReference or ownership authority.

### F5-0-I04

Provider upload side effects are not introduced in F5-0.

### F5-0-I05

F5-0 must not acquire a provider binding reservation from a PROVISIONAL AgentToolResult path.

### F5-0-I06

R7 same-execution/same-invocation resume identity is untouched.

### F5-0-I07

Provider cache expiry never changes R7 checkpoint fingerprints.

### F5-0-I08

Canonical bytes may later be protected by hydration leases, but provider-specific copies remain transient cache state.

---

# 14. F5-0 transaction boundaries

Provider-binding reservation/CAS and hydration-read lease creation are SQL-local transactions.

They do not attempt distributed ACID with a provider Files API.

F5-0 contains no provider external side effect.

Future F5-3/F5-4 external upload flow will be:

```text
SQL reserve PROCESSING
COMMIT
↓
external provider upload
↓
SQL activate ACTIVE
```

Unknown external outcome therefore remains a future reconciliation problem, not an F5-0 transaction.

---

# 15. Mandatory F5-0 tests

F5-0 must not close until tests prove all of the following.

## Migration

```text
f2h_r7_asset_compat
→ f5a_provider_hydration_leases
→ downgrade back to f2h
```

and:

```text
Alembic has exactly one head
```

## Binding schema

```text
provider_file_id is nullable

same:
(file_id, provider_name, provider_namespace)
cannot create two slots

ACTIVE without provider_file_id
→ rejected

PROCESSING without lease_token
→ rejected

PROCESSING without lease_expires_at
→ rejected

non-PROCESSING with binding lease
→ rejected
```

## Migration compatibility

```text
pre-F5 PROCESSING row
→ normalized to ERROR
→ revision increments

duplicate historical logical slots
→ migration fails explicitly
→ no silent row deletion
```

## Binding CAS

```text
two workers reserve same empty slot
→ one logical slot

stale revision
→ no mutation

wrong lease token activate/error
→ no mutation

expired ACTIVE lookup
→ not reusable

stale PROCESSING reclaim
→ one CAS winner
```

## Hydration lease

```text
active lease
→ visible to has_active_hydration_lease

expired lease
→ not active

foreign token release
→ cannot release

exact token release
→ lease removed

cleanup
→ deletes expired only
```

## R7/F1-F4 regression

Targeted regression must include:

```text
test_r7_j_real_network_exit_gate.py
test_f2h_r7_asset_compatibility.py
test_f4_message_asset_integration.py
test_f1_central_asset_migration.py
test_f2h_central_asset_migration.py
```

Then run:

```text
python -m pytest -q
```

---

# 16. F5-0 downgrade rule

Downgrade must not silently lose provider identity.

Before restoring:

```text
provider_file_id NOT NULL
```

the downgrade must fail closed if any binding row still has:

```text
provider_file_id IS NULL
```

or an active F5 PROCESSING reservation exists.

The caller must first settle/remove those F5-only rows.

After the precondition passes, downgrade may:

```text
drop file_asset_leases
drop F5 slot/check constraints
drop binding lease columns
restore provider_file_id NOT NULL
restore f2h schema
```

---

# 17. Explicit F5-0 non-goals

Do not implement in F5-0:

```text
trusted inference principal propagation        # F5-1
AssetHydrationService                          # F5-2
AsyncIterable[bytes] provider upload           # F5-3
Gemini Files API integration                   # F5-4
provider converter hydration                   # F5-5
provider-copy cleanup/reconciliation           # F5-6
F6 client/UI flow
F7 provider-generated output ingestion
F8 legacy cutover
```

Do not modify:

```text
R7 checkpoint schema
ResumePlan
ResumeClaim
PendingResumeTicket
events_router resume authority
F4 Message/FileReference atomic semantics
/v1/assets public contract
```

unless a new P0 is first demonstrated and the contract is explicitly re-frozen again.

---

# 18. F5-0 implementation order

When implementation begins:

```text
F5-0A
model + migration representation

F5-0B
provider binding slot/lease constraints

F5-0C
binding CAS repository primitives

F5-0D
file_asset_leases repository primitives

F5-0E
migration + concurrency + expiry tests

F5-0F
targeted R7/F1-F4 regressions

F5-0G
full repository CI
```

No provider call is allowed before F5-0 closes.

---

# 19. Frozen status

```text
FINAL R7 SYNC:
COMPLETE

combined code HEAD:
4b99f679e1ea7026215939901a47858b9cc9aae3

combined exit gate:
GREEN

Phase 5 consolidated:
40 passed

Linux full suite:
830 passed
1 skipped

Windows client:
68 passed

F1:
CLOSED

F2:
GREEN

F2-H:
CLOSED

F3:
CLOSED

F4:
CLOSED

F5-0:
CONTRACT RE-FROZEN
NOT IMPLEMENTED

F5-1+:
NOT IMPLEMENTED
```

The immutable implementation baseline for this freeze is `4b99f679`.

A later documentation-only commit containing this file is not a new runtime baseline.
