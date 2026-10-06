# CTX-F5-3I-B8-P2 — Explicit Promotion Caller Authority + Entry-Gate Freeze

Status: **CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION**  
Canonical workspace: Issue #15  
Policy: Issue #85 v2.5  
Independent roadmap / contract PRE-CLAIM: Issue #15 comment #5998028420  
Owner contract-only CLAIM: Issue #15 comment #6008645217

## 1. Frozen baseline and authority

```text
stage = CTX-F5-3I-B8-P2
release audit baseline = 89b248a9e29e09313ba7344bb6ba737249b2ec0f
development baseline = 4e30b39df362a67c0e95b410b44ae5dec1bc842c
development baseline Architecture #2168 / 37334618013 = GREEN/GREEN
parent CTX-F5-3I-B8 = LANDED / CANONICAL / HEALTHY
parent CTX-F5-3I-B8-P1 = LANDED / CANONICAL / HEALTHY
class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = CLOSED
production CLAIM = NONE
automatic promotion = CLOSED
F6 Personalization = CLOSED
merge authority = NONE
blocking P0/P1/P2 = 0/0/0
```

Drift from the independent audit baseline `89b248a9...` to the development
baseline `4e30b39d...` is one zero-production TV1-T11-A contract/evidence
commit. It has zero CTX production-path overlap and was independently classified
NON_MATERIAL before this CLAIM.

Exact changed-file maximum for P2:

1. `docs/context_future/CTX_F5_3I_B8_P2_TRUSTED_PROMOTION_CALLER_AUTHORITY_FREEZE_89B248A9.md`
2. `se/tests/architecture/test_ctx_f5_3i_b8_p2_trusted_promotion_caller_authority_freeze.py`

```text
docs delta = exact 1 NEW
architecture-evidence delta = exact 1 NEW
se/src/** delta = ZERO
cl/** delta = ZERO
config delta = ZERO
schema/migration delta = ZERO
runtime/API/container delta = ZERO
```

Any production, runtime, schema, migration, client, configuration, API, routing,
container, or scheduling change invalidates this contract CLAIM.

## 2. Current canonical disposition: caller count remains zero

The landed B8-P1 primitive is:

```python
await storage.promote_tool_response_payload_memory(
    source_ref=canonical_source_ref,
    owner_user_id=authenticated_owner_user_id,
)
```

P2 does not create a caller for that primitive.

At this freeze, production source under `se/src/**` contains **zero call sites**
of:

```python
.promote_tool_response_payload_memory(...)
```

The method definition in `StorageEngine` is a trusted internal primitive, not
an invocation trigger. Its existence does not imply that any lifecycle event,
tool result, provider completion, runtime, API, model, or capability is allowed
to call it.

The architecture evidence for P2 MUST continue to prove production caller count
equals zero.

## 3. First real caller requires a separate production PRE-CLAIM

A future first production caller is CLOSED until a new independent production
PRE-CLAIM freezes all of the following together:

- exact caller function and owning class/module;
- exact production path maximum;
- exact invocation reason and entry condition;
- exact authenticated owner provenance;
- exact `ContextSourceRef` provenance;
- synchronous/awaited versus separately authorized scheduling placement;
- failure/cancellation propagation;
- then-current UBQ accounting implications;
- then-current TBO/timeout implications;
- Issue #156 capability/routing implications if the path is Agent/model/capability
  visible;
- current-main drift and active path ownership.

That future PRE-CLAIM must be followed by a separate production CLAIM before any
production edit. P2 itself releases no production path.

## 4. Authenticated owner provenance is not source metadata

The preferred current authenticated principal source is canonical
`Identity.user_id`.

A future caller may use `Identity.user_id` only when it is supplied by the
then-current authenticated server request/runtime authority, or may use a
separately audited equivalently trusted server principal mapping.

The following MUST NOT independently mint promotion owner authority:

- `source_ref.owner_user_id`;
- Session id or Session metadata;
- Task id or Task metadata;
- Branch id or Branch metadata;
- Execution id or execution metadata;
- tool-call id or tool result metadata;
- client/model-supplied owner id;
- capability metadata;
- runtime-session identity;
- `AgentExecution.owner_instance_id`;
- future `agent_instance_id` without a separately released identity binding.

The caller-supplied authenticated owner and the canonical
`ContextSourceRef` remain separate inputs. B1/B5 source authority must
independently re-prove source ownership; the caller MUST NOT treat
`source_ref.owner_user_id` alone as authentication authority.

## 5. Explicit invocation only; implicit lifecycle promotion stays forbidden

Automatic promotion remains CLOSED.

A future first caller MUST NOT be inferred from or silently attached to:

- tool completion;
- capability completion;
- AgentToolResult COMMITTED observation;
- provider completion;
- retry, recovery, reconciliation, or resume;
- startup or shutdown;
- Session, Task, Branch, or AgentExecution lifecycle;
- event bus publication/subscription;
- ContextBuilder;
- retrieval;
- Working Set;
- ContextSnapshot;
- detached/background worker;
- timer or periodic scan.

No event, persistence transition, or durable source observation is itself a
promotion authorization.

## 6. No public, model, client, or capability surface in P2

P2 grants no:

- HTTP/FastAPI endpoint;
- WebSocket command;
- DirectChatRuntime caller;
- AgentRuntime caller;
- Tool registration;
- capability registration;
- model-visible promotion operation;
- `capability_id`;
- client protocol;
- DCS or Issue #156 routing authority.

If a later caller becomes Agent/model/capability-path visible, a fresh bilateral
audit with Issue #156 and the then-current UBQ owner is mandatory before the
production PRE-CLAIM may PASS.

## 7. Scheduling, UBQ, and TBO remain external

P2 creates no invocation/accounting identity and no resource policy.

It grants no:

- queue or background scheduling;
- detached task;
- TaskBudget admission;
- UBQ admission, charge, refund, reservation, or quota authority;
- timeout/retry budget;
- inference quota semantics;
- tool-call quota semantics.

A future caller on an Agent/capability critical path must re-audit then-current
UBQ and TBO authority. A future caller outside that path still needs explicit
scheduling ownership frozen before production CLAIM.

## 8. CAS, R11, AE, APR, and later CTX authority remain external

- CAS #74 retains ASSET/media/provider/ObjectStorage/lifecycle/deletion/GC
  authority. TOOL_RESPONSE_PAYLOAD remains opaque CTX source material.
- Issue #31/R11 retains transcript/checkpoint read-liveness, retention and
  destructive-GC authority.
- Issue #107/R12 is closed/canonical; no recovery or replay authority transfers
  into this stage.
- AE-R13-B is canonical and released its former active `se/src/main.py`
  ownership, but that release does not grant CTX `main.py` authority.
- APR #278 AgentInstance work remains separate; neither
  `AgentExecution.owner_instance_id` nor a future `agent_instance_id` is
  promotion owner authority without a separately released binding.
- Issue #156 retains Agent-only/capability-selection/routing/sandbox authority.
- F6 Personalization remains CLOSED.
- F7 pins/score/dedupe remains CLOSED.
- F9 Working Set/ContextSnapshot remains CLOSED.
- F10 CompactContext remains CLOSED.

The bounded TV1-T11-B request to project screenshot binary out of model-facing
`se/src/runtimes/agent/adapters/context.py` is not a CTX Memory-source mutation
and transfers no CTX ownership. Any execution-owner release for that request is
separate from P2 and does not authorize a Memory promotion caller.

## 9. No hidden fallback caller

A future implementation MUST NOT avoid the first-caller gate by:

- adding a helper with a different method name that ultimately invokes P1;
- routing through raw `StorageEngine.services`;
- constructing the promotion service directly;
- invoking B5 `.promote(...)` through a new container field;
- using an event listener or callback as an implicit caller;
- using a background job, timer, startup hook, or recovery hook;
- exposing a Tool/capability/API that reaches promotion indirectly.

Semantic equivalence to a caller counts as a caller and requires the same
production PRE-CLAIM.

## 10. Entry gate for the future caller

The minimum lawful transition after this P2 contract becomes canonical/healthy is
**not** "implement a caller."

The next production transition requires:

1. re-read Issue #15 and exact current main;
2. prove P2 is LANDED / CANONICAL / HEALTHY;
3. reconstruct the remaining F5 roadmap;
4. choose the smallest concrete first-caller proposal, or explicitly propose F5
   closure with the primitive intentionally dormant;
5. run a fresh independent roadmap/dependency/PRE-CLAIM audit;
6. freeze caller identity, path maximum, owner/source provenance,
   scheduling/failure semantics and UBQ/TBO/#156 implications;
7. record a separate production CLAIM only after independent release.

Until all seven conditions are satisfied:

```text
production PRE-CLAIM = CLOSED
production CLAIM = NONE
first production caller = NONE
automatic promotion = CLOSED
F5 closure = NOT YET DECLARED
F6 Personalization = CLOSED
merge authority = NONE
```

## 11. P2 exit gate

P2 may reach FINAL only when:

- exact changed paths remain 2 / 2;
- production/runtime/schema/migration/config/client delta remains zero;
- architecture evidence proves production caller count is still zero;
- exact-head Linux Architecture is GREEN;
- exact-head Windows Architecture is GREEN;
- independent P2 contract FINAL is PASS;
- no unresolved blocking review thread exists;
- no P0/P1 blocker exists;
- no MATERIAL drift invalidates this freeze.

Because P2 is genuinely contract/evidence-only and zero-production, it may use
Issue #15's standing conditional auto-merge exception only after all of those
exact gates are rechecked immediately before merge. P2 never authorizes a
production merge by implication.
