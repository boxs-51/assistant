# CTX-F5-3K — USER_WIDE Memory Storage Classification Contract

Status: CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION  
Canonical workspace: Issue #15  
Policy: Issue #85 v2.5 + stricter Issue #15 local rules  
Independent contract PRE-CLAIM: Issue #15 comment #6038025301  
Owner contract CLAIM: Issue #15 comment #6038040426

## 1. Frozen baseline and authority

```text
stage = CTX-F5-3K
name = USER_WIDE Memory Storage Classification Contract
development baseline = cdf270a926b154f40a0ae3f5db1d80cec4fd8d07
baseline Architecture #2290 / 37620056189 = GREEN/GREEN
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = HOLD / NOT RELEASED
production CLAIM = NONE
merge authority = NONE
```

Exact changed-file maximum:

1. `docs/context_future/CTX_F5_3K_USER_WIDE_MEMORY_STORAGE_CLASSIFICATION_CONTRACT_CDF270A9.md`
2. `se/tests/architecture/test_ctx_f5_3k_user_wide_memory_storage_classification_contract.py`

```text
exact changed paths = 2 NEW / 2
se/src/** delta = ZERO
cl/** delta = ZERO
schema/migration delta = ZERO
repository/model delta = ZERO
runtime/API/router delta = ZERO
ContextBuilder/Working Set/ContextSnapshot delta = ZERO
```

Any production, schema, migration, repository/model, runtime/API, client, retrieval,
ContextBuilder, or Agent-identity change invalidates this CLAIM.

## 2. Canonical predecessor

CTX-F5-3J is LANDED / CANONICAL / HEALTHY and reserves two logical future Memory
classifications:

```text
USER_WIDE
AGENT_PRIVATE
```

CTX-F5-3K does not reopen AGENT_PRIVATE. AGENT_PRIVATE production remains HARD HOLD
on canonical AIC stable Agent-instance identity.

Current canonical source remains owner-only:
- `MemoryRecord.owner_user_id`;
- `MemoryRecordRow.owner_user_id`;
- no durable `memory_scope`;
- no durable `agent_instance_id`.

Current canonical migration head at this freeze is:

```text
29a_crt1_capability_invocation_target
```

## 3. Legacy owner-only rows are not USER_WIDE by default

Existing durable owner-only Memory rows are legacy/unclassified owner-only records.

The future durable classification representation MUST preserve this distinction:

```text
NULL / absent future memory_scope
= LEGACY OWNER-ONLY / UNCLASSIFIED
!= USER_WIDE
!= AGENT_PRIVATE
```

The first production migration MUST NOT:
- assign a database default of USER_WIDE;
- assign an application default that rewrites persisted legacy state;
- backfill existing rows to USER_WIDE;
- infer AGENT_PRIVATE from Session/Task/Branch/AgentExecution metadata;
- rewrite, remint, relabel, or delete existing Memory rows.

A later explicit migration/classification policy is required before legacy rows can
be relabeled.

## 4. First production durable classification

The first post-3J production representation may implement exactly one explicit
durable scope value:

```text
USER_WIDE
```

AGENT_PRIVATE storage representation, `agent_instance_id`, per-Agent migration,
private-Memory access, and Agent lifecycle semantics remain CLOSED.

The first production representation should use a nullable durable classification
field with:
- no database default;
- no automatic application default for persisted legacy rows;
- no automatic backfill;
- a constraint that accepts only NULL legacy state plus explicitly released values.

If the then-current canonical migration head is still
`29a_crt1_capability_invocation_target`, the first implementation migration should
be its single linear child. If main advances first, a fresh PRE-CLAIM must re-resolve
the exact migration parent.

## 5. Identity and immutable replay semantics

Legacy Memory identities MUST remain stable.

The first classification implementation MUST NOT remint or rewrite existing
`memory_id` values merely because a scope representation is introduced.

The existing v1 Memory identity material remains the compatibility baseline.
An explicitly stored classification is immutable record material and MUST
participate in replay/conflict comparison.

Therefore an existing durable Memory record MUST NOT be silently changed from:
- legacy/unclassified -> USER_WIDE;
- USER_WIDE -> legacy/unclassified;
- USER_WIDE -> AGENT_PRIVATE.

A conflicting classification under the same durable Memory identity or promotion
authority must fail closed rather than converge silently.

## 6. Scope classification is not promotion authority

Memory scope classification does not mint source authority, reservation authority,
or promotion authority.

The first representation slice MUST NOT change:
- source-proof authority;
- promotion intent authority;
- reservation issue/recovery;
- atomic Memory admission;
- current P3 caller behavior;
- automatic promotion policy.

Caller-provided scope alone cannot authorize Memory creation or promotion.

The current promotion/admission chain may continue producing legacy/unclassified
owner-only records until a separately released trusted USER_WIDE classification
producer is claimed.

Any production caller that starts writing USER_WIDE requires its own fresh
PRE-CLAIM and must prove trusted owner/source authority independently.

## 7. Stored classification is not read visibility

The following remain CLOSED:
- persisted/global Memory listing;
- Memory search;
- Memory retrieval API;
- ranking/vector/semantic/NL retrieval;
- ContextBuilder injection;
- Working Set composition;
- ContextSnapshot persistence/use;
- automatic model visibility;
- cross-Agent sharing.

USER_WIDE storage classification does not mean every Agent of the owner can read
or receive the item.

A future read path must perform current owner/scope authorization independently.

## 8. External ownership remains unchanged

- Issue #31/R11 retains transcript/checkpoint read-liveness, retention, and
  destructive-GC authority.
- CAS #74 retains ASSET/FileAsset/FileBlob/ObjectStorage/provider lifecycle,
  grants, hydration, deletion, and GC.
- AE owns execution/recovery/lifecycle semantics.
- Issue #156 owns capability selection/routing/sandbox architecture.
- AIC/APR own Agent identity/profile/runtime authority.
- UBQ/TBO own resource/quota/scheduling/timeout policy.

CTX-F5-3K transfers none of those authorities.

## 9. Production stage that follows this contract

After this contract is LANDED / CANONICAL / HEALTHY, Issue #15 may perform a fresh
production PRE-CLAIM for a bounded USER_WIDE representation/persistence slice.

That audit must re-resolve:
- exact current main;
- exact canonical migration head;
- production paths;
- global migration-head evidence paths;
- domain/repository replay semantics;
- exact tests;
- open schema/migration collisions.

The first production slice should remain representation/persistence only unless a
separate PRE-CLAIM explicitly releases a trusted USER_WIDE producer.

## 10. Still CLOSED

CTX-F5-3K does not release:
- any `se/src/**` implementation;
- Alembic migration;
- `MemoryRecord` field changes;
- `MemoryRecordRow` field changes;
- repository changes;
- promotion/admission changes;
- P3 changes;
- USER_WIDE producer/caller;
- AGENT_PRIVATE implementation;
- `agent_instance_id`;
- Memory read/search/access APIs;
- ContextBuilder/Working Set/ContextSnapshot;
- F6/F7/F8/F9/F10/F11/F12 production work;
- migration/backfill/erasure/revocation/delete behavior.

## 11. Exit gate

CTX-F5-3K may reach independent FINAL only when:
- exact changed paths remain 2 NEW / 2;
- production/runtime/schema/migration/repository/model/API/client delta remains zero;
- architecture evidence proves current canonical Memory source remains owner-only;
- architecture evidence proves no durable `memory_scope` or `agent_instance_id`
  exists before the separately released production stage;
- architecture evidence proves current migration head 29a at this frozen baseline;
- architecture evidence freezes NULL/absent != USER_WIDE;
- architecture evidence freezes no-default/no-backfill/no-relabel semantics;
- architecture evidence freezes identity stability + immutable replay conflict semantics;
- architecture evidence freezes source-proof/admission/P3 and read/model-visibility fences;
- exact-head Linux Architecture is GREEN;
- exact-head Windows Architecture is GREEN;
- independent CTX-F5-3K contract FINAL is PASS;
- unresolved blocking review threads = 0;
- blocking P0/P1 = 0;
- no MATERIAL drift invalidates the freeze.

Because this candidate is genuinely zero-production contract/evidence-only, the
standing Issue #15 conditional zero-production merge exception may apply only
after the complete exact-head FINAL/CI/review/current-main guard is clean.
