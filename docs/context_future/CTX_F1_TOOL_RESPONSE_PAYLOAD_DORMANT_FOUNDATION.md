# CTX-F1 — ToolResponsePayload Dormant Foundation Contract

**Issue:** #15  
**Base:** `main@f5c77e93929a3d1fc8dfbfed8a046378bb9a3894`  
**Branch:** `work/ctx-f0-early-f5c77e93`  
**Status:** ACTIVE / DORMANT FOUNDATION / NO SQL MIGRATION

## 1. Early-storage decision

CTX-F1 does **not** consume or copy the parked Central Asset ObjectStorage line.

Until CAS is replayed/re-audited on a current baseline:

- F1 owns only the ToolResponsePayload domain contract;
- no SQL table or Alembic revision is allocated;
- no object-store adapter is wired;
- no file/media lifecycle is introduced;
- the only reference repository implementation is process-local and intended for
  contract/testing use;
- durable physical storage is deferred to a later F1 storage substage after a
  fresh CAS/R11 dependency audit.

This closes the early-activation storage ambiguity without inventing a second
Central Asset implementation.

## 2. Source eligibility

A ToolResponsePayload may be created only from a **COMMITTED** tool result.

PROVISIONAL / ambiguous / remote-unknown results are not promotable into this
domain.

F1 does not decide whether a tool result is COMMITTED. It consumes that verdict
from the existing Agent/Capability authority.

## 3. Exact replay identity

The source identity tuple is:

```text
source_result_id
+ invocation_id
+ execution_id
+ tool_call_id
+ logical_capability_id
+ payload_schema_version
+ content_digest
```

`payload_id` is a deterministic domain hash of this tuple.

Consequences:

- true replay of the same committed result and same canonical content converges
  on one payload ID;
- two separate committed invocations of the same logical tool with identical
  output bytes receive different payload IDs;
- changing content for the same source result changes the derived payload ID and
  is treated as conflicting source reuse by a repository;
- provider-local aliases, implementation IDs, provider file IDs, URLs and local
  paths are excluded from identity authority.

## 4. Canonical content

F1 canonical JSON uses UTF-8, sorted keys, compact separators and
`ensure_ascii=False`.

The content digest is SHA-256 over canonical JSON bytes.

F1 accepts JSON-compatible payload content only. Future binary/file outputs are
not encoded into this domain; Central Asset remains their authority.

## 5. Domain object

The dormant domain record contains:

```text
payload_id
source_result_id
invocation_id
execution_id
tool_call_id
logical_capability_id
source_commit_state = COMMITTED
payload_schema_version
content_digest
canonical_bytes
content (process-local reference only; durable physical storage deferred)
content_type
owner_user_id?
session_id?
metadata
created_at
```

The object is deeply immutable after creation for payload content and metadata. Both content and metadata are restricted to a structural canonical JSON input domain: object keys must be strings, arrays must be Python lists at admission, and tuple/set/frozenset/custom containers, bytearray/custom mutable leaves, and non-finite numeric values are rejected. Accepted list/object containers are recursively frozen to tuple/MappingProxyType internally. The process-local reference repository retains canonical logical content only to make the dormant contract executable; this is not durable storage authority.

Owner/session values are provenance only in F1. They do not yet authorize
cross-session context retrieval; that belongs to later Context Access stages.

## 6. Repository semantics

The reference repository API is append-only:

- `put(payload)`
- `get(payload_id)`
- `get_by_source_result(source_result_id)`

No update/delete/GC API is introduced in F1.

Admission integrity is revalidated at both model construction and repository put. Repository admission also verifies that the in-memory representation is recursively frozen, so unvalidated model-copy container substitutions cannot become repository authority. The repository recomputes canonical content bytes, digest, size and deterministic payload ID, checks non-empty source identities and requires explicit `source_commit_state=COMMITTED`. This protects first insert as well as replays, including unvalidated `model_copy(update=...)` objects.

Required replay behavior:

1. identical payload ID + identical object -> return existing object;
2. same payload ID + different object -> fail closed;
3. same source_result_id + different payload ID -> fail closed;
4. different source_result_id may store identical content because invocation
   identity is intentionally distinct.

The process-local repository is not durable authority and must not be wired into
current Agent execution.

## 7. Migration serialization disposition

`P1-CTX-F1-MIGRATION-1` is satisfied for this substage by allocating **no
migration at all**.

Current single Alembic head remains owned by the repository's active persistence
sequence. If a later F1 substage needs durable SQL, Issue #15 must re-read #31
and current main and explicitly claim the next linear revision before creating
it.

## 8. Forbidden integration

F1 must not:

- modify Agent checkpoint/transcript tables or readers/writers;
- alter CapabilityInvocation / AgentToolResult commitment semantics;
- inject payloads into ContextBuilder;
- create Memory records;
- create FileReference/FileBlob/FileAsset records;
- create provider-native bindings;
- persist an ObjectStorage key as logical payload identity;
- implement retention/GC.

## 9. Exit gate for dormant F1 foundation

- exact identity helper implemented;
- canonical digest deterministic;
- COMMITTED-only source gate implemented;
- immutable ToolResponsePayload contract implemented;
- append-only in-memory reference repository implemented;
- replay/conflict tests cover same-source and different-invocation behavior;
- no migration/schema/runtime wiring delta;
- Issue #15 auditor re-check requested before any durable storage substage.
