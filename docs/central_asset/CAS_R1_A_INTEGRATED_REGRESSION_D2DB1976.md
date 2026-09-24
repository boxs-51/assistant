# CAS-R1-A — Integrated F1→F4 regression and authority freeze

Primary issue: #68  
Opening canonical main: `d2db19769bb328683e78bad9c5580919dbdeae5f`  
Branch: `work/cas-r1-a-d2db1976`  
Stage: CAS-R1-A

## Purpose

CAS-R1-A is an integration/evidence stage over the already-landed Central Asset
F1→F4 implementation after CTX-F2C landed. It does not add a new asset feature.

The stage proves that current-main convergence still preserves the following
authority chain:

```text
canonical asset identity / lifecycle
    = CAS

context ASSET projection
    = CTX read/derive-only view over already-authorized CAS evidence

Agent retention/reachability
    = R11 authority

provider-native hydration
    = future F5 authority
```

No one of those authorities may silently substitute for another.

## Exact baseline

```text
main / stage parent
  d2db19769bb328683e78bad9c5580919dbdeae5f

CTX-F2C merged PR
  #59

audited CTX-F2C HEAD
  9b2ffaf911e966606faa468d6b78b8b9d126c4ac
```

The missing separately-visible post-merge Architecture run at CAS-R1 OPEN time
is not treated as GREEN evidence. Exact-head CI on this CAS-R1-A branch is the
dynamic revalidation authority for this stage.

## Frozen invariants

### 1. Canonical identity

Only:

```text
asset_id
asset://<asset_id>
```

is canonical CAS identity.

Provider file IDs, provider URIs, object keys, signed URLs, local paths, base64
and raw bytes remain non-canonical transport/storage representations.

### 2. F1 storage authority

The canonical model remains:

```text
FileAsset
  -> FileBlob
  -> FileReference
  -> FileProviderBinding (secondary schema only)
```

`provider_file_id` must never move onto `FileAsset`.

Migration lineage remains:

```text
18a_cas_r0_assets
  -> 17a_r11_checkpoint_cutover
  -> 16a_r11_transcript_representation
```

### 3. F2 lifecycle/readability

Only an owner-authorized READY FileAsset backed by a READY FileBlob is readable.

The public delete operation remains fail-closed. CAS-R1 does not authorize
READY -> DELETING or physical object destruction.

STAGING-abort cleanup remains a separate ingest-failure safety operation and is
not physical GC authority.

### 4. F3/F4 reference and message authority

Canonical asset-bearing message persistence must keep Message and
MESSAGE_CONTENT FileReference creation in the same unit of work.

Foreign/non-READY asset evidence must fail before durable message/reference
publication.

Session deletion and destructive asset-bearing message edits remain blocked
while canonical references require explicit release authority.

### 5. CTX-F2C boundary

`project_asset_source(...)` remains:
- READY-only;
- `authority_id = asset_id`;
- `authority_version = None`;
- exact `asset://<asset_id>`;
- projection-only;
- free of SQL/repository/object-store/AssetService dependencies.

FileAsset revision remains metadata and does not become CTX ASSET identity.

### 6. R11 boundary

Agent persistence/retention may reason about Agent roots and reachability, but
Agent runtime code must not acquire direct CAS physical-lifecycle authority.

In particular, Agent runtime must not directly own/import:
- FileAssetRecord;
- FileBlobRecord;
- FileReferenceRecord;
- FileProviderBindingRecord;
- ObjectStorageDriver.

R11 reachability alone is not permission to physically delete CAS bytes.

### 7. Pre-F5 provider guard

F5 remains CLOSED.

Current production code must not call the dormant
`create_provider_binding(...)` / `get_active_provider_binding(...)` repository
surface outside the Central Asset repository itself.

Canonical asset-bearing history must fail before provider execution with the
existing ASSET_HYDRATION_REQUIRED boundary.

## Executable evidence

`se/tests/architecture/test_cas_r1_integrated_regression.py` freezes the
cross-boundary invariants above on one exact branch HEAD.

This supplements, rather than replaces, the existing focused suites:
- `test_f1_central_asset_storage.py`;
- `test_f2_asset_service.py`;
- `test_f3_asset_api.py`;
- `test_f4_message_asset_integration.py`;
- `test_ctx_f2_source_adapters.py`.

## Hard non-goals

CAS-R1-A does not:
- activate FileProviderBinding runtime lifecycle;
- hydrate/upload provider-native files;
- implement CAS physical GC/reconciliation;
- implement R11 destructive deletion;
- implement R12 recovery/lease semantics;
- open F6/F7/F8;
- introduce CTX ASSET search/read/runtime wiring;
- weaken any fail-closed reference boundary.

## Exit evidence required

CAS-R1-A may hand off only after:
1. exact changed-file scope is audited;
2. integrated regression is GREEN;
3. full Architecture Linux + Windows is GREEN on exact HEAD;
4. no unresolved CAS-R1 P0 remains;
5. every CAS-R1 P1 is fixed or explicitly carried with owner/stage;
6. ownership matrix is re-frozen against current Issue #15 / Issue #31 state;
7. exact HEAD is recorded in Issue #68.

No merge is authorized by this document.
