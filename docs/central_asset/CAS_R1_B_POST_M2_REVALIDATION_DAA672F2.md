# CAS-R1-B — Post-M1/M2 convergence revalidation

Primary issue: #68  
Exact parent: `daa672f2a93ad16cc3c6fe0a2e9edc0b3821b9ad`  
Branch: `work/cas-r1-b-daa672f2`

## Purpose

CAS-R1-B revalidates the already-landed Central Asset F1→F4 authority after
repository-wide M1 and M2 convergence. It is an evidence/regression stage only;
it does not open a new asset feature or destructive lifecycle.

Current main contains:
- CTX F3A/F3B/F4A;
- R11 E2/F0/F1-A/F1-B;
- CAS-R1-A integrated regression.

No CAS FileAsset/FileBlob/FileReference/FileProviderBinding/ObjectStorage
production file changed between the CAS-R1-A merge and this exact parent.

## Refreshed ownership matrix

| Surface | Current authority | Explicitly not owned here |
| --- | --- | --- |
| CAS identity | `asset_id`, `asset://<asset_id>` | provider ID/URI, object key, local path |
| CAS readability/lifecycle | FileAsset + FileBlob + AssetService | CTX, R11 |
| CAS durable references | FileReference + canonical message integration | R11 reachability |
| Provider binding schema | CAS | runtime hydration remains F5 CLOSED |
| CTX F2C | READY-only ASSET projection | storage/lifecycle/release/GC |
| CTX F3A/F3B/F4A | Session/Task/Branch discovery and structural access | ASSET discovery/read/hydration |
| R11 F1-B | Agent-owned RETAIN/CANDIDATE/FAIL_CLOSED dry-run | CAS physical deletion/release |
| R11 F1-C | RESERVED only | CAS models, object-store GC, READY asset transition |

## Post-M2 invariants

### CTX

`discovery.py`, `discovery_collection.py`, and `access.py` remain structural
Session/Task/Branch surfaces. They must not:
- consume `ContextSourceKind.ASSET`;
- invoke `project_asset_source`;
- read CAS repositories/object storage;
- gain FileAsset/FileBlob/FileReference/FileProviderBinding authority;
- hydrate provider files;
- release or garbage-collect assets.

### R11

`gc_dry_run.py` remains Agent-only and non-destructive:
- no CAS model imports;
- no ObjectStorage dependency;
- no `asset://` reinterpretation;
- no DELETE/truncate path;
- dry-run candidacy is evidence only and never CAS release permission.

### Migration lineage

Current lineage extends CAS safely:

```text
19a_r11_query_order_indexes
  -> 18a_cas_r0_assets
  -> 17a_r11_checkpoint_cutover
  -> 16a_r11_transcript_representation
```

19a may add R11 query-order indexes only. It must not mutate CAS tables or
perform object-store I/O.

## R11-F1-C destructive boundary

From the CAS side, a future R11-F1-C may only mutate Agent-owned persistence
after its own transactional revalidation.

It must not:
- transition READY FileAsset state;
- delete FileBlob or object-store bytes;
- release/delete FileReference rows;
- mutate FileProviderBinding lifecycle;
- run CAS reconciliation;
- infer that R11 RETAIN/CANDIDATE classification is CAS delete authority.

If a future Agent deletion can affect canonical CAS reference lifetime, R11
must fail closed until an explicit CAS-owned retain/release handoff contract is
defined and audited. No such handoff is opened by CAS-R1-B.

Therefore CAS physical GC/reconciliation and READY asset deletion remain CLOSED
even if R11-F1-C is later released within Agent-owned persistence scope.

## F5-0 refreeze

```text
F5-0 = CLOSED
F5 provider hydration = CLOSED
FileProviderBinding runtime lifecycle = CLOSED
provider upload = CLOSED
CAS physical GC/reconciliation = CLOSED
```

Opening any of these requires a separate stage/claim and fresh audit.

## CAS-R1-B evidence

The existing
`se/tests/architecture/test_cas_r1_integrated_regression.py`
continues to prove F1→F4 identity/readability/reference/message/F5 guards and is
extended in CAS-R1-B to freeze:
- CTX F3A/F3B/F4A non-ASSET ownership;
- R11 19a migration lineage without CAS mutation;
- R11 F1-B non-destructive Agent-only boundary.

## Exit gate

CAS-R1-B may be released only after:
1. exact parent/current-main relationship is recorded;
2. exact changed-file scope is audited;
3. Architecture Linux + Windows is GREEN on exact CAS-R1-B HEAD;
4. no current-main CAS P0 exists;
5. every P1 is fixed or explicitly carried;
6. ownership matrix above is independently rechecked;
7. F5-0/F5 CLOSED boundary is explicitly preserved.

No merge or future destructive authority is authorized by this document.
