# CTX-F5-3J — Memory Scope Classification Contract

Status: CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
Canonical workspace: Issue #15
Policy: Issue #85 v2.5
Independent roadmap / contract PRE-CLAIM: Issue #15 comment #6024838940
Owner contract CLAIM: Issue #15 comment #6030522861

## 1. Frozen baseline and authority

stage = CTX-F5-3J
development baseline = 5b0a6c868c62a50f6a78de1484a032cbcce346f8
baseline Architecture #2216 / 37521737064 = GREEN/GREEN
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = CLOSED
production CLAIM = NONE
merge authority = NONE

CTX-F5-3J freezes Memory scope classification only. It implements no new
Memory field, repository behavior, migration, read path, API, retrieval path,
Agent identity, or model-visible behavior.

Exact changed-file maximum:

1. docs/context_future/CTX_F5_3J_MEMORY_SCOPE_CONTRACT_5B0A6C86.md
2. se/tests/architecture/test_ctx_f5_3j_memory_scope_contract.py

exact changed paths = 2 NEW / 2
se/src/** delta = ZERO
cl/** delta = ZERO
schema/migration delta = ZERO
repository/model delta = ZERO
runtime/API/router delta = ZERO
ContextBuilder/Working Set/ContextSnapshot delta = ZERO
registry delta = ZERO

Any production, runtime, schema, migration, repository/model, API/router,
client, retrieval/index, ContextBuilder, registry, or Agent identity edit
invalidates this contract CLAIM.

## 2. Current source fact remains owner-only

At this freeze, canonical source has MemoryRecord.owner_user_id and
MemoryRecordRow.owner_user_id, with no canonical durable agent_instance_id
field on either representation.

That fact does not imply that all existing Memory is user-wide. It means legacy
and current owner-only records have not yet been durably classified into an
Agent-private scope.

CTX-F5-3J does not alter those records or their identity material.

## 3. Reserved Memory scope taxonomy

Two future logical Memory scopes are reserved:

USER_WIDE
AGENT_PRIVATE

These are CTX access/lifecycle classifications. They do not create new
authentication, promotion, retrieval, or model-visible authority.

### USER_WIDE

USER_WIDE means a Memory item is owned by exactly one authenticated user
without an Agent-private binding.

USER_WIDE storage classification does not mean every Agent of that user may
automatically read the item, the item is automatically model-visible, or the
item is automatically injected into ContextBuilder or a Working Set.

Agent visibility remains a separate authorization and retrieval decision.

### AGENT_PRIVATE

A future durable AGENT_PRIVATE Memory item must bind exactly:

(owner_user_id, agent_instance_id)

The pair is a scope identity, not a caller-provided authorization shortcut.
The owner remains authenticated user authority; Agent identity must come from
separately canonical Agent registration/AIC authority.

## 4. Trusted Agent-instance identity dependency

A future agent_instance_id used by CTX MUST be server-resolved from separately
canonical Agent registration/AIC authority.

It MUST NOT be granted or inferred from:
- model output;
- request body;
- source metadata;
- Memory metadata;
- Session, Task, or Branch metadata;
- client identity or connection identity;
- AgentDefinition.name;
- AgentExecution.agent_id;
- AgentExecution.owner_instance_id;
- promotion caller input by itself.

AIC-0 durable Agent-instance identity is a HARD prerequisite for any
AGENT_PRIVATE schema/runtime implementation.

APR may consume or define Agent runtime/profile contracts, but APR does not
transfer Agent identity implementation authority into CTX.

## 5. Legacy owner-only Memory rows

Existing owner-only Memory rows MUST NOT:
- be silently classified as AGENT_PRIVATE;
- receive an inferred/default Agent instance;
- be assigned to an Agent based on historical Session/Task/Branch metadata;
- be relabeled by model output, request metadata, or a caller-provided scope.

A separately released migration/classification policy is required before any
schema migration, backfill, scope relabel, Agent-instance assignment, or
destructive erasure behavior based on new scope.

Until such a stage is released, legacy rows remain governed by their current
canonical owner/provenance semantics.

CTX-F5-3J itself does not rewrite, migrate, backfill, relabel, or delete any row.

## 6. Promotion and source authority remain unchanged

Memory scope classification does not mint Memory admission authority.

Caller-provided scope or Agent identity never creates promotion authority.
The existing CTX source-proof/reservation/admission lineage remains
authoritative for current TOOL_RESPONSE_PAYLOAD promotion, including the
B1/B3/B4/H-B2/B5/B8 chain.

The canonical P3 production caller remains separate from 3J and MUST NOT gain
a Memory-scope or Agent-instance field through 3J.

P3 = LANDED / CANONICAL / HEALTHY
P3 source PR = #305@4160696ca7dc9a9172cd7db9bfa8e8afc90e5d87
P3 canonical main = 3c1022fd57a4ab16efebb6a0487fe5e663e0a081
P3 post-merge Architecture #2240 / 37570885508 = GREEN/GREEN
P3 wave = #317 / IW-2026-10-07-06 / COMPLETE / CLOSED
automatic promotion = CLOSED

3J does not authorize a second P4 caller, AgentRuntime trigger, Tool/capability
trigger, lifecycle hook, event subscriber, queue, or background promotion.

## 7. Storage classification is not read visibility

The following remain CLOSED:
- persisted/global Memory listing/search;
- user-wide cross-session index/search;
- ranking, semantic, or vector retrieval;
- model Working Set injection;
- ContextBuilder injection;
- ContextSnapshot persistence/use;
- cross-Agent sharing;
- automatic model visibility.

Issue #15 must assign and independently release a separate exact CTX stage
before user-wide cross-session index/search receives a CLAIM.

A future read path must re-check current owner/scope authorization. Stored
classification alone is insufficient authorization.

## 8. Lifecycle, erasure, and external ownership

Before AGENT_PRIVATE destructive APIs may open, a separately released stage
must define suspend/delete/export/revocation/erasure behavior for private
Memory and the effect of Agent-instance lifecycle changes.

CTX-F5-3J owns no physical transcript, checkpoint, asset, object, or provider
deletion.

- Issue #31/R11 retains transcript/checkpoint read-liveness, retention, and destructive-GC authority.
- CAS #74 retains asset/object/provider lifecycle, grants, deletion, and GC.
- AE/R12/R13 execution/recovery/lifecycle authority remains external.
- Issue #156/DCS retains capability selection/routing/sandbox authority.
- UBQ/TBO retain resource, quota, scheduling, and timeout policy.
- AIC/APR retain their Agent identity/profile/runtime authority.
- F6 Personalization remains CLOSED.

No dependency statement transfers implementation authority.

## 9. Later CTX stages remain closed

3J does not release user-wide cross-session index/search implementation,
AGENT_PRIVATE schema/runtime implementation, migration or legacy-row
classification implementation, Memory read/search/ranking, Personalization,
pins/score/dedupe, Working Set or ContextSnapshot, CompactContext,
DefaultChatAgent convergence, or migration/quality exit work.

AGENT_PRIVATE production = HARD HOLD ON CANONICAL AIC-0 IDENTITY
user-wide cross-session search = SEPARATE FUTURE STAGE / NOT CLAIMED
F6 Personalization = CLOSED
F7 = CLOSED
F9 = CLOSED
F10 = CLOSED

## 10. 3J exit gate

CTX-F5-3J may reach independent FINAL only when:
- exact changed paths remain 2 NEW / 2;
- production/runtime/schema/migration/repository/model/API/client delta remains zero;
- architecture evidence proves current MemoryRecord and MemoryRecordRow remain owner-only with no agent_instance_id;
- architecture evidence proves the USER_WIDE / AGENT_PRIVATE contract and trusted identity fences are present;
- exact-head Linux Architecture is GREEN;
- exact-head Windows Architecture is GREEN;
- independent CTX-F5-3J contract FINAL is PASS;
- unresolved blocking review threads = 0;
- blocking P0/P1 = 0;
- no MATERIAL current-main drift invalidates the freeze.

Because 3J is genuinely contract/evidence-only and zero-production, Issue #15's
standing conditional zero-production auto-merge exception may apply only after
the full exact-head FINAL/CI/review/drift gate is rechecked immediately before
merge. Wave enrollment, if any, supersedes standalone merge behavior.

No production authority follows from 3J becoming canonical.
