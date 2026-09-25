# CAS-F5-0 — Provider Hydration Contract Re-Freeze

Authority: Issue #74

Exact reviewed and landed lineage:

```text
Issue #68 = CLOSED / COMPLETED
reviewed parent main = 25c7453252753fae0ea65ad712a8e89eeac4f802
reviewed PR #75 HEAD = 1e6d30829f23e045877f0e2e3b909a7274efaddd
candidate Architecture #1217 / run 36102375063 = GREEN / GREEN
merge commit / landing main = 51f5c67bdbd3e503b13542c7c82980b87f420d5f
post-merge Architecture #1218 / run 36102980541 = GREEN / GREEN
current integration baseline after R11 Repair A = c50d0670ae80aac60efc3557eeff8f78a4a8e425
current-main Architecture #1220 / run 36104672013 = GREEN / GREEN
F5-0 = LANDED / CONTRACT RELEASED
production F5 foundation = RELEASED ONLY TO F5-1 SCOPE
merge authorization for later PRs = NONE
```

The historical activation point `main@200aa3dc...` remains part of the audit trail, but it is not the reviewed parent of the final PR #75 candidate and must not be used as the exact integration baseline for F5-0 release evidence.

## 1. Canonical identity

Canonical CAS identity remains:

```text
asset_id
asset://<asset_id>
```

Provider-specific identity remains secondary only:

```text
provider_name
provider_namespace
provider_file_id
provider_uri
```

A provider file id/URI must never replace or become the persisted canonical CAS identity.

A canonical `GatewayAttachment` with `asset_id` must continue to reject:
- `provider_file_id`;
- base64 bytes;
- raw bytes;
- non-`asset://<asset_id>` URI;
- any non-`asset` source.

Hydration must therefore create a transient provider-facing projection rather than rewrite canonical persisted message content.

## 2. Hydration authority boundary

F5 production orchestration, when separately released, may own:
- authorization of one canonical READY asset for hydration;
- creation/reuse of FileProviderBinding runtime state;
- reading canonical bytes from ObjectStorage after owner + READY validation;
- invoking a provider-owned file upload primitive;
- mapping the resulting provider representation back to the canonical asset through FileProviderBinding;
- transiently projecting provider-ready attachment state into the provider request.

F5 must not own:
- provider retry/fallback/deadline/capability policy beyond the narrow upload operation;
- CTX search/read/personalization authority;
- R11 retention/deletion authority;
- R12 lease/recovery authority;
- READY asset physical deletion;
- CAS blob GC/reconciliation;
- provider remote-file deletion/reclamation;
- cross-provider cleanup.

## 3. Trusted inputs

Hydration may start only from trusted runtime authority, never client metadata.

Required trusted inputs:
- authenticated/authorized `owner_user_id`;
- canonical `asset_id`;
- provider selection from runtime/provider configuration;
- provider namespace identifying the credential/account/project tenancy boundary.

Client-supplied values must not become authority for:
- `provider_file_id`;
- `provider_uri`;
- `provider_namespace`;
- raw provider metadata;
- object-store keys;
- CAS object bytes.

The existing generic provider file handler is a provider API primitive only. Its payload-level `file_bytes` upload path is not CAS hydration authority and must not be used as the canonical authorization boundary.

## 4. Canonical preconditions

Before any provider upload or binding reuse:

1. FileAsset exists.
2. FileAsset is owned by the authorized user.
3. FileAsset state is exactly `READY`.
4. Referenced FileBlob exists.
5. FileBlob state is exactly `READY`.
6. Blob integrity fields required by READY state are present.
7. Hydration reads bytes through canonical ObjectStorage using server-side CAS state.
8. No provider identity supplied by request content is trusted.

Foreign, missing, non-READY, deleted, quarantined, or otherwise unreadable assets fail closed before provider side effects.

## 5. Binding identity and reuse key

Canonical hydration slot key:

```text
(asset_id, provider_name, provider_namespace)
```

Remote provider identity:

```text
(provider_name, provider_namespace, provider_file_id)
```

The remote identity remains secondary and must be unique when known.

A binding may be reused only when all of the following hold:
- slot key matches;
- state is `ACTIVE`;
- provider identity is present;
- expiration policy considers it valid;
- provider verification policy, when required, passes;
- binding content fingerprint still matches the current canonical FileBlob.

Model identity is not part of the default binding key. If a provider's file handles are model/capability scoped, that scope must be represented by `provider_namespace` or a separately audited schema field before reuse.

## 6. Current-schema gaps that must be fixed before production F5

Current `FileProviderBindingRecord` is schema-only and has two material gaps for safe runtime hydration.

### 6.1 Durable pre-side-effect claim gap

Current `provider_file_id` is NOT NULL.

That prevents a correct durable `PROCESSING` claim from being committed before the remote provider upload returns an id.

Production F5 must not use fake/placeholder provider ids.

Required implementation plan:
- permit a PROCESSING binding claim before remote identity exists;
- enforce that ACTIVE requires a non-null provider identity;
- preserve uniqueness of real provider identity.

### 6.2 Content-fingerprint gap

Current binding stores `file_id` but not the hydrated blob/content fingerprint.

Because FileAsset carries a mutable `blob_id` field at schema level, F5 must not assume that `file_id` alone proves provider content freshness.

Production F5 must durably bind the provider representation to canonical content authority, at minimum:
- source `blob_id`; and
- source SHA-256 or equivalent verified fingerprint.

An ACTIVE binding whose recorded content fingerprint differs from the current READY blob must not be reused.

## 7. Single-flight and concurrency contract

The system must guarantee at most one live hydration claimant for one slot key.

Live states for this invariant:

```text
PROCESSING
ACTIVE
```

The current schema does not enforce uniqueness of live bindings by
`(file_id, provider_name, provider_namespace)`.

Before production F5, implementation must use the exact DB-enforced live-slot authority frozen in the normative P1-closure addendum below; implementation choice is not open.

An in-memory/process-local lock is not sufficient authority.

Required behavior:
- existing valid ACTIVE -> reuse;
- existing PROCESSING -> return/propagate HYDRATION_IN_PROGRESS or equivalent; do not start a duplicate upload;
- EXPIRED/ERROR may permit a new claim only under the retry-safety rules below;
- concurrent claims must not create two ACTIVE bindings for the same slot.

## 8. Lifecycle state machine

F5 runtime may use only these non-destructive states:

```text
PROCESSING -> ACTIVE
PROCESSING -> ERROR
ACTIVE -> EXPIRED
EXPIRED -> PROCESSING/new claim
ERROR -> PROCESSING/new claim only when retry is proven safe
```

`DELETING` and `DELETED` already exist in schema but remain CLOSED for F5 implementation.

F5 must not introduce provider remote deletion or CAS physical deletion merely because those states exist.

Every mutable binding transition must be revision-checked / compare-and-set or otherwise transactionally protected.

## 9. Provider side-effect uncertainty and retry safety

Provider upload is an external side effect.

The Gemini primitive currently uses resumable upload and returns a provider file id, but no repository evidence establishes a general idempotency key for file creation.

Therefore automatic re-upload is forbidden after an uncertain remote outcome.

Required outcome classes:

### SAFE_NO_REMOTE_COMMIT
The adapter proves the provider did not create a remote file.
- binding may move to ERROR;
- retry may be allowed.

### REMOTE_SUCCESS_KNOWN
Provider returned a stable remote identity.
- persist provider identity and transition PROCESSING -> ACTIVE;
- if persistence temporarily fails, retry persistence of the same known result before starting any new upload.

### REMOTE_OUTCOME_UNKNOWN
Timeout/crash/transport ambiguity means remote creation may have occurred but no reliable provider identity is durably known.
- fail closed;
- do not automatically re-upload;
- binding enters the explicit durable UNKNOWN representation frozen in the normative P1-closure addendum below;
- recovery/reconciliation must be explicit and must not silently invoke remote deletion.

A stale PROCESSING row is not permission to upload again.

## 10. Expiry and verification

`expires_at` and `last_verified_at` remain secondary runtime evidence.

`get_active_provider_binding(...)` currently filters state only and does not itself prove:
- non-expiry;
- content fingerprint freshness;
- remote existence;
- owner authorization.

The F5 service must perform those checks before reuse.

Provider verification should use provider-owned metadata/read APIs and must not reinterpret provider metadata as canonical CAS authority.

## 11. Provider-facing transient projection

Persisted canonical message/history remains asset-based.

Before provider inference:
1. detect canonical asset content;
2. hydrate/reuse bindings under F5 authority;
3. produce a transient provider-facing attachment/request representation;
4. call the provider;
5. never persist provider identity as canonical message identity.

The current pre-F5 `ASSET_HYDRATION_REQUIRED` guard remains authoritative until production F5 is separately released.

Replacing that guard is part of production F5 and requires its own exact-head audit.

## 12. Cross-track boundary refresh

### CTX / Issue #15
CTX remains read/derive/projection authority.
Provider hydration and FileProviderBinding lifecycle do not transfer to CTX.
Any future CTX dependency on hydrated provider state requires an explicit cross-issue contract.

### R11 / Issue #31
R11 currently has an open blocking concurrency issue:
`P1-R11-F1C-SEMANTIC-EDGE-RACE-1`.

F5 must not:
- absorb R11 retention/deletion authority;
- let R11 GC classification delete provider bindings or CAS assets;
- interpret Agent deletion as CAS release.

If Agent lifecycle may affect canonical CAS reference lifetime, retain/release remains a separately audited CAS handoff.

## 13. Delete / cleanup boundary

Still CLOSED:
- provider remote-file deletion;
- provider cleanup/reclamation;
- binding DELETING/DELETED runtime flow;
- READY asset physical deletion;
- blob GC/reconciliation;
- orphan provider-file cleanup.

A successful hydration does not grant cleanup authority.

## 14. Production implementation release gate

Production F5 remains CLOSED until independent audit passes this contract and a durable checkpoint confirms:

- exact canonical main;
- binding slot key and provider namespace semantics;
- durable pre-upload PROCESSING claim design;
- live-slot uniqueness / cross-process serialization;
- content fingerprint schema plan;
- lifecycle transition rules;
- external side-effect outcome taxonomy;
- retry rules;
- transient provider projection contract;
- owner/READY authorization flow;
- provider adapter interface;
- no-delete/no-GC exclusions;
- migration plan and regression matrix;
- no unresolved F5 P0/P1.

Only after that release may production F5 code begin.

## 15. Initial regression matrix

Contract/evidence tests must freeze:
- canonical identity remains asset-only;
- provider ids remain secondary;
- FileProviderBinding schema remains dormant until release;
- current workflow still fails canonical assets with ASSET_HYDRATION_REQUIRED;
- provider upload/delete primitives are provider-owned and not CAS authority;
- CAS application code does not call provider remote delete;
- CTX/R11 boundaries remain unchanged;
- current schema gaps are explicitly visible so implementation cannot bypass them.

## 16. Normative P1-closure addendum

This section is normative and **supersedes any less-specific wording in sections 3, 6, 7, 8, 9, 14, or 15**.

### 16.1 P1-CAS-F5-0-PROVIDER-NAMESPACE-AUTHORITY-1 — exact freeze

Production F5 must add this server configuration field:

```text
ProviderConfig.file_binding_namespace: str | None = None
```

Authority rules:
- server/operator configured only; never request-derived;
- opaque stable logical provider-tenancy identifier;
- not the API key, not a hash/fingerprint of the API key, and not provider file metadata;
- stable across process restarts;
- credential rotation for the same logical provider account/project keeps the same namespace;
- different credential/account/project tenancies must use different namespaces;
- F5 fails closed with `HYDRATION_PROVIDER_SCOPE_UNCONFIGURED` when absent;
- production F5 must not fall back to repository/schema default `"default"`;
- `"default"` may be used only by a separately audited provider contract explicitly declared single-tenant; no current production F5 provider has that exemption.

For the first Gemini F5 slice, the value is the stable server-owned identity of the Google credential/project tenancy used by that provider instance.

The hydration slot key is exactly:

```text
(file_id, provider_name, provider_namespace)
```

Two provider instances representing distinct credential/account/project scopes must not share one namespace and therefore cannot share an ACTIVE binding slot.

### 16.2 P1-CAS-F5-0-LIVE-SLOT-SERIALIZATION-1 — exact freeze

Historical binding rows are retained. Production F5 must add:

```text
live_claim_token: str | None
```

Exact live-claim values:

```text
PROCESSING -> "LIVE"
ACTIVE     -> "LIVE"
UNKNOWN    -> "LIVE"
EXPIRED    -> NULL
ERROR      -> NULL
```

The migration must add this exact DB uniqueness authority:

```text
UNIQUE(file_id, provider_name, provider_namespace, live_claim_token)
```

All live rows use the same non-null token `"LIVE"`; historical EXPIRED/ERROR rows use NULL. Therefore the database admits at most one live claimant for one slot while preserving historical rows.

An in-memory/process-local lock is optimization only and is never authority.

Exact claim/replacement ordering:

1. authorize owner + READY FileAsset + READY FileBlob;
2. derive provider_namespace from server `ProviderConfig.file_binding_namespace`;
3. re-read any live slot row;
4. PROCESSING -> return `HYDRATION_IN_PROGRESS`; no upload;
5. UNKNOWN -> return `HYDRATION_OUTCOME_UNKNOWN`; no upload;
6. ACTIVE -> validate current expiry and exact `source_blob_id + source_sha256` fingerprint;
7. valid ACTIVE -> reuse;
8. invalid/expired ACTIVE -> perform a revision/CAS-protected transition in one transaction:
   ```text
   ACTIVE -> EXPIRED
   live_claim_token -> NULL
   ```
9. COMMIT the ACTIVE -> EXPIRED transition before any replacement PROCESSING insert;
10. if the ACTIVE -> EXPIRED CAS loses a race, re-read the current live winner and do not upload;
11. only after successful live-slot release may a new PROCESSING row be INSERTed with `live_claim_token="LIVE"`, null provider identity, and the current content fingerprint;
12. COMMIT the replacement PROCESSING claim;
13. only after that commit may any remote upload mutation be attempted.

If concurrent replacement/INSERT loses the unique constraint race, it must re-read the winner and must not start another upload.

ACTIVE -> EXPIRED is metadata/lifecycle retirement only. It does not delete the remote provider file, does not reclaim provider storage, and does not authorize CAS/blob deletion. The historical provider identity remains durable evidence on the EXPIRED row.

Production F5 must also make `provider_file_id` nullable for PROCESSING/UNKNOWN and enforce that ACTIVE requires a non-null provider identity.

### 16.3 Content fingerprint — exact freeze

Every binding created by production F5 must durably record:

```text
source_blob_id
source_sha256
```

ACTIVE reuse requires both to match the current READY FileBlob. File id alone is insufficient freshness authority.

### 16.4 P1-CAS-F5-0-REMOTE-OUTCOME-AUTHORITY-1 — exact freeze

Production F5 must extend the binding state check with:

```text
UNKNOWN
```

The provider adapter boundary must return/raise a typed outcome equivalent to:

```text
ProviderUploadOutcome.kind =
    SAFE_NO_REMOTE_COMMIT
    | REMOTE_SUCCESS_KNOWN
    | REMOTE_OUTCOME_UNKNOWN

ProviderUploadOutcome.provider_file_id: str | None
ProviderUploadOutcome.provider_uri: str | None
ProviderUploadOutcome.metadata: mapping
```

CAS must not infer outcome classes from generic exception strings.

#### SAFE_NO_REMOTE_COMMIT

This class is allowed only when **no remote mutating upload/finalize request was sent**.

Examples include local validation/configuration/CAS stream failures before the first provider mutation request.

Transition:

```text
PROCESSING -> ERROR
live_claim_token -> NULL
```

A later new PROCESSING claim may be permitted.

#### REMOTE_SUCCESS_KNOWN

A stable provider id is known.

Transition:

```text
PROCESSING -> ACTIVE
live_claim_token remains "LIVE"
provider_file_id = returned stable id
```

If ACTIVE persistence fails after known remote success, retry persistence of the same known outcome. Do not perform another upload.

#### REMOTE_OUTCOME_UNKNOWN

Any timeout/cancellation/crash/transport ambiguity after a remote mutating request may have been sent, without a stable returned provider identity, is UNKNOWN.

Durable representation is exactly:

```text
state = "UNKNOWN"
live_claim_token = "LIVE"
metadata_json["upload_outcome"] = "REMOTE_OUTCOME_UNKNOWN"
provider_file_id = NULL unless a stable id is actually known
```

Initial F5 behavior:
- fail closed with `HYDRATION_OUTCOME_UNKNOWN`;
- do not automatically re-upload;
- do not clear the live claim because of age alone;
- do not call provider remote delete;
- no automatic UNKNOWN recovery exists in the initial F5 slice.

Only a separately audited recovery/reconciliation stage may resolve UNKNOWN and release its live claim.

If the process crashes after the durable PROCESSING claim and before it can classify the remote result, stale PROCESSING is treated as UNKNOWN-equivalent for retry authority: **no automatic re-upload**.

### 16.5 Initial implementation migration/state plan

The first production F5 migration must, relative to the then-current Alembic head:
- make `provider_file_id` nullable;
- add `source_blob_id`;
- add `source_sha256`;
- add `live_claim_token`;
- add UNKNOWN to the binding state constraint;
- add the exact live-slot unique constraint above;
- add/check invariants so ACTIVE requires provider identity and live states carry `"LIVE"`.

The exact Alembic revision id/down_revision is chosen only when the implementation branch is cut, so it extends the actual then-current canonical migration head rather than a stale pre-integration head.

### 16.6 Regression requirements closing the F5-0 P1s

Before production F5 release, executable evidence must prove:
- missing `file_binding_namespace` fails before remote side effects;
- distinct server-configured provider tenancies yield distinct slots;
- ambiguous `"default"` fallback is rejected;
- two concurrent PROCESSING claims cannot both acquire the same live slot;
- historical EXPIRED/ERROR rows do not block a legitimate new claim;
- PROCESSING/ACTIVE/UNKNOWN do block a second live claim;
- unknown remote outcome cannot trigger a second upload;
- generic timeout after possible mutation maps to UNKNOWN, not SAFE;
- only pre-mutation failure maps to SAFE_NO_REMOTE_COMMIT;
- ACTIVE reuse requires matching source_blob_id + source_sha256;
- expired ACTIVE can CAS-transition to EXPIRED + NULL live claim before replacement;
- fingerprint-mismatched ACTIVE can CAS-transition to EXPIRED + NULL live claim before replacement;
- losing ACTIVE-retirement/replacement claimant re-reads the winner and does not upload;
- concurrent replacement attempts cannot create two live claimants;
- historical EXPIRED provider identity remains preserved;
- ACTIVE retirement does not imply provider remote delete/reclamation;
- no provider remote delete, READY asset deletion, or CAS physical GC is introduced.

These requirements close only the F5-0 contract blockers. They do not authorize production implementation or merge.

## Final F5-0 status

```text
F5-0 contract = LANDED / INDEPENDENTLY RELEASED
reviewed parent = 25c7453252753fae0ea65ad712a8e89eeac4f802
audited HEAD = 1e6d30829f23e045877f0e2e3b909a7274efaddd
landing main = 51f5c67bdbd3e503b13542c7c82980b87f420d5f
post-merge Architecture #1218 = GREEN / GREEN
current F5-1 integration baseline = c50d0670ae80aac60efc3557eeff8f78a4a8e425
F5-1 foundation scope = RELEASED
provider upload/orchestration = CLOSED
workflow hydration wiring = CLOSED
remote cleanup = CLOSED
READY asset deletion = CLOSED
UNKNOWN automatic recovery = CLOSED
CAS physical GC = CLOSED
merge authorization for later PRs = NONE
```
