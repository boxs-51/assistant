# CTX-F5-3L-C0 — AGENT_PRIVATE Memory authority and identity boundary (contract freeze)

Canonical owner: Issue #15. Governance: Policy #85 v2.5 plus stricter Issue #15 rules.
Independent contract/evidence PRE-CLAIM: Issue #15 comment #6061005795 (PASS / RELEASED).
Owner exact-two-path CONTRACT/EVIDENCE CLAIM: Issue #15 comment #6061036214.
Development baseline: `main@7332af469c074fb4331eee841f0ee121c7738f3e`.
Baseline Architecture: `#2376 / 37778284808 = GREEN/GREEN`.
Canonical predecessor: CTX-F5-3K-P2, PR #368, IW-2026-10-07-28/#370 COMPLETE/CLOSED.

## 1. Exact authority and non-authority

```text
stage = CTX-F5-3L-C0
name = AGENT_PRIVATE Memory Authority Boundary
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = HOLD / NOT RELEASED
production CLAIM = NONE
merge authority = NONE
exact changed paths = 2 NEW / 2
third path = PROHIBITED / replacement PRE-CLAIM
se/src/** delta = ZERO
cl/** delta = ZERO
schema/migration delta = ZERO
Memory model/repository/admission delta = ZERO
runtime/API/router delta = ZERO
ContextBuilder/Working Set/ContextSnapshot delta = ZERO
registry delta = ZERO
```

Exact new files ONLY:
1. `docs/context_future/CTX_F5_3L_AGENT_PRIVATE_MEMORY_AUTHORITY_BOUNDARY_CONTRACT_7332AF46.md`
2. `se/tests/architecture/test_ctx_f5_3l_agent_private_memory_authority_boundary_contract.py`

This contract freezes future verification requirements; it implements no `AGENT_PRIVATE` runtime, schema, durable `AgentInstance`, caller path, read or model visibility. Neither PRE-CLAIM, CLAIM nor subsequent FINAL/merge grants any production authority. A future production slice requires its own exact-main independent production PRE-CLAIM, CLAIM, ownership/migration audit, tests, FINAL, and explicit production Integration Wave authorization.

## 2. Existing canonical state — not a private-memory implementation

CTX-F5-3J/K/P1/P2 is the current Memory classification/admission lineage:
- `MemoryRecord` and `MemoryRecordRow` store `owner_user_id` and optional `memory_scope`; neither currently stores `agent_instance_id`.
- The only currently accepted explicit persisted `memory_scope` value is `USER_WIDE`. Legacy NULL remains unclassified; `AGENT_PRIVATE` is NOT accepted in the existing model or SQL constraint.
- The first trusted ISSUED durable admission in `DurableMemoryPromotionAdmission` creates a `USER_WIDE` record. CONSUMED replay loads the validated existing winner and preserves its stored NULL or USER_WIDE scope; `memory_records_replay_equivalent` remains mandatory.
- Existing `MEMORY_IDENTITY_DOMAIN = "ctx-memory-v1"` and the ordered v1 `memory_id` inputs (`owner_user_id`, `promotion_authority_id`, `source_context_source_id`, `content_digest`, `memory_schema_version`) remain unchanged. Scope is immutable replay material, not a new v1 identity input.
- No schema default, backfill, relabel, inferred Agent binding, remint, replay rewrite, or legacy scope migration is permitted by this stage.

AIC-0-R0 is a canonical identity *contract*, not a runtime authority. The current `AgentRegistry` is keyed by `AgentDefinition.name`, `AgentExecution.agent_id` is definition/execution selection, and `AgentExecution.owner_instance_id` belongs to AE lease/recovery. None is a stable user-owned Agent instance. AIC-0-P1 durable instance storage, trusted lookup and lifecycle transitions are a HARD EXTERNAL PREREQUISITE to any CTX AGENT_PRIVATE production implementation.

## 3. Future trusted owner / Agent-instance resolution boundary (NOT implemented)

The future authoritative private Memory owner tuple is precisely:

```text
(authenticated owner_user_id, server-resolved agent_instance_id)
```

The server must resolve the owner from authenticated user authority, then resolve an opaque stable Agent instance through separately released AIC identity and current lifecycle authority; a request, model, Memory metadata, Context source, Session/Task/Branch ID, client/connection, `AgentDefinition.name`, `AgentExecution.agent_id`, `AgentExecution.owner_instance_id`, `AgentProfile`, or DCS capability selection cannot create or substitute private ownership.

The future resolver/authorization handshake MUST fail closed if:
1. the user is unauthenticated, mismatched, or lacks ownership;
2. the Agent instance is missing, unknown, stale, suspended (`SUSPENDED`) or deleted (`DELETED`);
3. the instance/definition-version binding cannot be proved or conflicts with the current AIC lifecycle;
4. the caller supplies an untrusted owner or Agent ID rather than a separately verified, server-derived binding;
5. a source entitlement, participation proof, current grant, or provenance check cannot be verified.

`ACTIVE` alone is not a broad grant: the owner, lifecycle, source proof and operation-specific CTX read/write policy must all pass. The same `AgentDefinition` under two users must map to distinct private identities. Two Agent instances of the same user and definition must also remain isolated by default. No implicit cross-Agent Memory access arises from messages, delegation, shared definition, or mutual session participation.

## 4. Future source-proof, promotion, immutable replay and revocation

- CTX will require a trusted source snapshot/provenance, owner match, immutable promotion authority/reservation and proof that the exact Agent instance participated in or was explicitly granted that source. Client/model-provided source tags, a cached Context projection, and an AIC message are not such proof.
- A future private promotion design must distinguish ISSUED first-winner admission from CONSUMED replay. Existing first-winner `USER_WIDE` semantics remain unchanged until a separately released new private producer path is designed. ISSUED + unexpected existing winner remains a conflict; REVOKED remains fail closed.
- CONSUMED replay must validate and compare the exact prior winner and its immutable content/source/metadata/schema/scope; stored scope alone never overrides the reservation or bypasses `memory_records_replay_equivalent`. Missing/corrupt/mismatched winners must fail closed. Replay may not relabel NULL/USER_WIDE as `AGENT_PRIVATE`, remint IDs, backfill, rewrite, or create a second record.
- Suspension/revocation must deny newly requested private promotions and reads. AIC `SUSPENDED` and `DELETED` lifecycle are external identity decisions; CTX must separately define access invalidation, private Memory retention/export/erasure, historical-Memory classification and source revocation before any production activation. Deletion never silently reassociates old private Memory with a new instance.
- Source authorization and CAS asset grants must be revalidated when used: a previously cached digest or ContextSnapshot cannot extend access after revocation. User policy may authorize sharing only through an explicit, current read gate; no transitive Agent-to-Agent permission.

## 5. Strict ownership / dependency matrix

| Owner | Authority retained outside this CTX contract |
|---|---|
| AIC / Agent registration, AIC-0-P1 | Durable AgentInstance identity `(owner_user_id, agent_instance_id)`, owner-bound registration/lookup, lifecycle transitions, compatibility migration |
| APR | AgentDefinition/AgentProfile/runtime preference and future execution-binding contract; no Agent instance minting or CTX Memory permission |
| AE / #107 / #31 R11 | Task/Branch/Execution, checkpoint/revision/lease/recovery, `owner_instance_id`, transcript liveness and retention handoff; no CTX Memory authorization |
| CTX / #15 | Future Memory classification, provenance, admission, read/list/search/sharing policy, private Memory erasure and Context working-set authorization only after distinct releases |
| CAS / #74 | Asset identity/grants, provider hydration, source asset permission, deletion and physical GC; references/digests do not confer asset read authority |
| #156 / DCS / CRT / SBX / Tools | Capability exposure, selected tools, target routing, sandbox/file/process execution; no inferred Agent/private Memory grant |
| UBQ / #141 and TBO | Resource admission/charging and Task eligibility/orchestration; not Memory or Agent identity authority |
| AAT / AIC messaging | Automation activation and message routing, not cross-Agent Memory sharing |
| CTX F6–F12 | Personalization, Pins, execution continuity, Working Set/ContextSnapshot, CompactContext, DefaultChatAgent and exit/quality each require separate staged authority |

Memory content storage cannot become a second canonical owner of transcript or CAS asset bytes. Any runtime identity persistence, source-proof service, schema/Alembic migration, API/router read/list/search, model-visible ContextBuilder injection or destructive retention transition is outside these two new files.

## 6. Missing gates, future stage sequencing and acceptance evidence

This is a *design boundary only*. A future stage inventory must separately resolve:
1. AIC-0-P1 durable identity, trusted owner lookup and ACTIVE/SUSPENDED/DELETED semantics — external HARD HOLD for CTX private production.
2. CTX-owned production PRE-CLAIM for schema/representation and migration lineage (including existing SQL memory-scope check and current migration head); no `AGENT_PRIVATE` value or `agent_instance_id` column is introduced here.
3. Independently released CTX source participation/grant-proof and first-winner producer policy; preserve existing USER_WIDE producer and legacy NULL CONSUMED replay.
4. Independently released private Memory read/list/search/sharing/revocation and, later, Working Set model visibility; no automatic/background AgentRuntime promotion.
5. Separate CTX deletion/export/erasure, R11/AE transcript retention and CAS asset-grant/physical-GC handoff.
6. F6 Personalization, F7 Pins/score/dedupe, F8 Execution Continuity, F9 Working Set/ContextSnapshot, F10 CompactContext, F11 DefaultChatAgent and F12 quality/exit retain RESERVED / NOT RELEASED production gates.

Future positive/negative evidence must cover same template/two users, same user/two instances, wrong-owner/missing/suspended/deleted instance, corrupted binding, revoked source/grant, source not participated/granted, immutable replay under retries/races, legacy NULL and USER_WIDE classification preservation, no owner/instance substitution from AgentDefinition/AE/session/client/model and no cross-Agent read without a current explicit grant.

The namespace registry status debt (`docs/ROADMAP_NAMESPACE_REGISTRY.md` still refers to CTX-F0 as active) requires **separate docs authority**, NOT a third file in this CLAIM.

## 7. Contract/evidence-only FINAL and integration gates

FINAL requires exact TWO NEW files, zero production/runtime/schema/migration/repository/model/API/client delta, direct architecture evidence of current source facts and closed fences, fresh exact-head Linux+Windows Architecture GREEN, no blocking review threads or P0/P1, and independent contract FINAL PASS. If enrolled in an Integration Wave, even a zero-production candidate cannot auto-merge outside that wave; its frozen wave manifest/authority governs integration. Otherwise only Issue #15's narrowly scoped zero-production conditional auto-merge rule applies after all exact-main/head/evidence guards. A contractual release never authorizes private Memory implementation by implication.
