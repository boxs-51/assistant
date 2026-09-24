# CTX-F2C — Dormant Central Asset Source Projection

**Primary issue:** #15  
**Base:** `main@3c6ffa06d4dc8b75c9cb03f8026e3a78a978ebb3`  
**Branch:** `work/ctx-f2c-asset-0853f9bf`  
**Status:** REFRESHED ON CURRENT MAIN / DORMANT PROJECTION ONLY

## 1. Entry gate

F2C opens only after all of the following are true:

- CTX-F2A is landed and post-merge GREEN;
- CTX-F2B is landed and post-merge GREEN;
- CAS-R0 has explicitly handed canonical asset evidence to CTX;
- R11-E1 has landed on canonical main without CTX path overlap;
- Architecture #1123 on exact `main@0853f9bf` is GREEN/GREEN.

The F2C implementation is intentionally smaller than asset discovery, reading,
hydration, indexing, or model injection.

## 2. Authority separation

The source authority chain remains:

```text
Central Asset lifecycle/read authority
!=
CTX source projection
!=
Context discovery/read API
!=
model Working Set
```

Central Asset continues to own:

- `FileAsset`;
- `FileBlob`;
- `FileReference`;
- provider bindings;
- object-storage lifecycle;
- canonical owner authorization;
- READY/readability state;
- `asset://<asset_id>`.

CTX-F2C owns only conversion of already-authorized canonical descriptor evidence
into a dormant `ContextSourceRef`.

## 3. Accepted evidence

The adapter consumes an already-loaded descriptor shape equivalent to:

```text
asset_id
owner_user_id
filename
mime_type
size_bytes
sha256
state
uri
origin_type
revision
```

The adapter does not load this evidence itself.

The upstream CAS authority must already have established that the descriptor
belongs to the requesting owner and that both FileAsset and FileBlob are
readable.

## 4. Projection identity

The canonical CTX identity is:

```text
source_kind       = ASSET
authority_id      = asset_id
authority_version = None
owner_user_id     = canonical CAS owner
```

`FileAsset.revision` is lifecycle evidence only.

Changing revision must not change `context_source_id`.

Neither `asset://<asset_id>` nor a provider ID, object key, local path, signed
URL, blob ID, checksum, or FileReference ID may replace `asset_id` as source
authority.

## 5. Eligibility

Projection fails closed unless:

```text
asset_id is a canonical non-empty string
owner_user_id is a canonical non-empty string
state == READY
uri == asset://<asset_id>
revision is an integer >= 0 and not bool
size_bytes is None OR an integer >= 0 and not bool
```

The adapter does not attempt to repair, trim, reinterpret, or hydrate malformed
evidence.

## 6. Output

A successful projection creates:

```text
ContextSourceRef(
    source_kind=ASSET,
    authority_id=asset_id,
    authority_version=None,
    owner_user_id=owner_user_id,
    source_state=READY,
)
```

Descriptive metadata carries:

```text
filename
mime_type
size_bytes
sha256
uri
origin_type
file_asset_revision
```

Metadata may change without changing the canonical ASSET context-source
identity.

## 7. Hard non-scope

F2C must not:

- import SQLAlchemy or storage repositories into the CTX adapter;
- query FileAsset/FileBlob/FileReference;
- open object storage;
- mutate Central Asset lifecycle state;
- implement provider hydration;
- create provider bindings;
- release or garbage-collect assets;
- infer authority from client attachment metadata;
- register the adapter into ContextBuilder or Agent runtime;
- implement discovery/search/read/ranking APIs;
- create Working Set or ContextSnapshot behavior;
- promote assets to Memory;
- reinterpret R11 transcript identity.

## 8. Regression gate

The implementation must prove:

1. canonical READY evidence projects deterministic ASSET identity;
2. revision changes preserve `context_source_id`;
3. non-READY evidence fails closed;
4. noncanonical asset/owner IDs fail closed;
5. URI mismatch fails closed;
6. invalid revision fails closed;
7. invalid size fails closed;
8. unknown size remains representable as `None`;
9. production adapter has no storage/object-store/runtime dependency;
10. all prior F2A/F2B SESSION/TASK/BRANCH/TRANSCRIPT/TRP regressions remain GREEN.

## 9. Exit gate

F2C is not merge-ready until:

- exact branch scope is audited;
- targeted regression coverage is GREEN;
- exact-head Architecture Linux/Windows is GREEN;
- independent Issue #15 audit reports no open P0/P1;
- canonical main has not drifted, or the candidate is refreshed and revalidated.

Even after F2C lands, discovery/read APIs remain CTX-F3/F4 work and are not
implicitly activated.


## Integration refresh

The audited F2C semantics were replayed onto exact canonical
`main@3c6ffa06d4dc8b75c9cb03f8026e3a78a978ebb3`.

The production adapter and architecture-test blobs are unchanged from the
previous audited F2C candidate `6d9ad4fd1882d9fe9f0e341bae78af13d0ffe512`.
Only this contract records the refreshed integration base/history.

The intervening canonical-main drift is disjoint from the F2C-owned adapter/test
paths. This refresh does not authorize merge and does not open F3/F4 ASSET
consumption.
