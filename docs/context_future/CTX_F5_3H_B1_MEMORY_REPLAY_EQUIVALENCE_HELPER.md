# CTX-F5-3H-B1 — Public Memory replay-equivalence helper

Primary workspace: Issue #15 / CTX  
Policy: Issue #85 v2.5

## Exact CLAIM baseline

```text
baseline = b5d52785617d92d06707cb616f0b4748b7c216bb
parent H-B0 corrective = LANDED / CANONICAL / HEALTHY
current-main Architecture #1633 = GREEN/GREEN
released production files = exactly 2
schema/migration authority = CLOSED
runtime/API/source wiring = CLOSED
H-B2 production CLAIM = CLOSED
```

Independent production release: Issue #15 comment #5914066828.

## Objective

Promote the existing private Memory replay-equivalence semantics to one reusable
public domain helper without changing replay meaning, Memory identity, persistence,
transaction ownership, or promotion/admission behavior.

The public authority is:

```python
def memory_records_replay_equivalent(
    left: MemoryRecord,
    right: MemoryRecord,
) -> bool:
    ...
```

## Frozen semantics

The helper preserves the exact existing immutable replay material:

- compare canonical immutable Memory material;
- ignore exactly top-level created_at;
- preserve current source_ref_snapshot.source_created_at datetime normalization;
- preserve canonical JSON type distinctions;
- perform no I/O;
- perform no repository access;
- perform no mutation;
- mint no identity;
- change no persistence or transaction semantics.

A private canonical-byte helper may remain only as an implementation detail behind
this one public equivalence authority. The old private `_same_immutable_record`
comparison authority must not remain.

## Repository migration

`InMemoryMemoryRecordRepository.put(...)` and durable
`DurableMemoryRecordRepository` replay/convergence both consume
`memory_records_replay_equivalent(...)`.

No repository may import or call `_same_immutable_record`.

Existing exact replay still converges to the committed winner. Conflicting immutable
replay still raises the existing `MemoryRecordConflictError` behavior.

## Explicit non-delta

```text
MemoryRecord schema/field set = UNCHANGED
Memory identity/digest algorithms = UNCHANGED
create_memory_record semantics = UNCHANGED
SQLite admission transaction semantics = UNCHANGED
promotion reservation semantics = UNCHANGED
exception mapping = UNCHANGED
schema/migration delta = ZERO
runtime/API/source wiring = CLOSED
non-SQLite expansion = CLOSED
H-B2 production CLAIM = CLOSED
```

## Evidence

Mandatory evidence includes:

1. records differing only in top-level `created_at` are equivalent;
2. conflicting immutable material is not equivalent;
3. equivalent source datetime offsets preserve the existing normalization;
4. in-memory exact replay still converges and immutable conflict still fails closed;
5. existing CTX Memory persistence integration tests continue to prove durable replay convergence/conflict;
6. durable repository imports and calls the public helper;
7. the private `_same_immutable_record` semantic authority is absent;
8. prior H-A/H-B0 architecture evidence is updated to current public-helper source facts without weakening historical closed boundaries;
9. no schema/migration/identity/digest/transaction semantic delta;
10. Linux + Windows Architecture must be GREEN/GREEN on exact candidate HEAD.

## Cross-track overlap

UBQ-2 PR #171 currently changes two CTX migration-head integration tests, including
`se/tests/integration/test_ctx_f5_2_memory_persistence.py`, but it does not change
either H-B1 production file. H-B1 does not edit that integration test; the existing
test still runs as part of the full suite. R12-F0 and CAS-F7-0 remain path/authority
disjoint from this helper migration.

Any requirement for a third production file or for schema, migration, admission
service/error, transaction, runtime, API, source, or non-SQLite changes invalidates
this CLAIM and returns H-B1 to audit.
