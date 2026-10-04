# CTX-F5-3I-B6 — Internal Storage-Service Activation Boundary

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.  
Independent PRE-CLAIM release: Issue #15 comment #5979047768.

## Status

```text
stage = CTX-F5-3I-B6
class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION
exact development baseline = 522b543e66f309085729e57a2248258b7c4f2599
baseline Architecture #2005 / 37194583170 = GREEN/GREEN
parent CTX-F5-3I-B5 = LANDED / CANONICAL / HEALTHY
contract PRE-CLAIM = PASS / RELEASED
contract CLAIM = TAKEN
production PRE-CLAIM = CLOSED
production CLAIM = NONE
runtime/container/API/model-callable authority = NONE
F6 Personalization authority = NONE
production/runtime/schema/migration delta = ZERO
merge authority = NONE
```

B6 freezes the first activation boundary for the canonical
TOOL_RESPONSE_PAYLOAD -> durable Memory promotion service. It does not activate
that service in production.

Follow-up lifetime-fence amendment: independent narrow release #5980357259 on
`main@c6b312342509de00eb24f27e1bc435bd5d0d9400` corrects retained-reference
revocation semantics after late P2 review. The amendment remains zero-production.

The landed B5 service is
`DurableToolResponsePayloadMemoryPromotion`. B6 preserves the complete B1 ->
B3/B4 -> H-B2 authority chain and defines how a later, separately released
production slice may construct that service without creating a second storage,
session, routing, quota, API, or lifecycle authority.

## 1. Canonical current state

The current repository already contains:

- `DurableToolResponsePayloadSourceAuthority`;
- `DurablePromotionReservationIssuer`;
- `DurablePromotionReservationRecovery`;
- `DurableToolResponsePayloadPromotionOrchestration`;
- `DurableMemoryPromotionAdmission`;
- `DurableToolResponsePayloadMemoryPromotion`.

The B5 composer accepts only:

```text
source_ref + owner_user_id
```

and performs:

```text
canonical B3/B4 reserve/recover handoff
-> exact returned PromotionReservation
-> one fresh handoff.content_snapshot
-> canonical H-B2 admission
-> exact durable MemoryRecord
```

B6 does not alter these semantics.

## 2. Preferred first activation seam

The canonical first activation seam is the existing internal storage-service
lifecycle, not a new application/runtime/public surface.

Current `StorageEngine` already owns:

```text
initialize drivers
-> connect drivers
-> initialize services
-> initialize repositories
-> STARTED
```

and already exposes an internal `StorageEngine.services` registry.

Therefore a later B6 production slice, if separately released, should prefer:

```text
available configured SQLite driver
-> public sqlite_driver.get_session
-> construct canonical promotion dependency chain
-> register one canonical internal service entry in StorageEngine.services
```

A new `ApplicationContainer` field, `main.py` bootstrap seam, runtime,
FastAPI dependency, tool registration, or public API is not required by this
contract and remains CLOSED.

## 3. Canonical construction chain

Future production activation must preserve exactly this authority order:

```text
available configured SQLite driver
-> public sqlite_driver.get_session
-> DurableToolResponsePayloadSourceAuthority
-> DurablePromotionReservationIssuer
-> DurablePromotionReservationRecovery
-> DurableToolResponsePayloadPromotionOrchestration(
     source_authority,
     issuer,
     recovery,
   )
-> DurableMemoryPromotionAdmission
-> DurableToolResponsePayloadMemoryPromotion
```

All components that require SQL session access consume the same public
`SQLiteDriver.get_session` session-context source.

The activation layer may construct dependencies. It must not reimplement their
business rules.

## 4. B4 recovery is mandatory

`DurableToolResponsePayloadPromotionOrchestration` currently permits an
optional recovery argument for construction compatibility. That optionality is
not activation authority.

The canonical B5 replay path depends on B4 recovery:

```text
fresh B2 re-proof
-> exact intent
-> trusted non-minting durable-winner recovery
-> ISSUED or CONSUMED exact PromotionReservation envelope
-> H-B2 first admission or exact replay
```

Therefore a B6 activation must pass a real
`DurablePromotionReservationRecovery`.

Constructing the orchestration with `reservation_recovery=None` is not an
equivalent production activation and is prohibited.

## 5. One canonical session-context source

Future activation reuses public `SQLiteDriver.get_session`.

It must not:

- create another SQLAlchemy engine;
- call `async_sessionmaker`;
- expose or reuse the driver's private `_session_factory`;
- create a hidden session factory;
- reinterpret `uow_factory` as the B1/B4/H-B2 session-context contract;
- share one live `AsyncSession` across independent service calls.

Each lower-layer service retains its already-landed fresh-session/transaction
semantics.

## 6. Storage lifecycle ownership

Construction may occur only after the configured SQLite driver is connected and
reported available.

If SQLite is not configured or unavailable:

```text
TOOL_RESPONSE_PAYLOAD Memory promotion service = NOT ACTIVATED
fallback backend = NONE
degraded in-memory authority = NONE
```

The promotion service owns no independent thread, task, event loop, start
method, stop method, engine, or connection pool.

Its usable lifetime is bounded by the owning `StorageEngine` driver lifecycle.
On storage shutdown, `StorageEngine.services` is cleared and the SQLite driver
owns disposal of its engine.

## 6.1. Retained-reference lifetime fence — follow-up amendment

Late post-merge review of the zero-production B6 contract found that registry
cleanup alone does not revoke a service object already retained by trusted
internal code. This amendment corrects the future activation contract; it does
not implement production activation.

`StorageEngine.services.clear()` is registry cleanup only. It is explicitly
**not** retained-reference revocation. Likewise, disposing the SQLite engine is
not the B6 service-lifetime authority.

Any later production activation must bind every published B6 service object to
the exact owning `StorageEngine` activation generation. The activation layer
must provide a generation-bound revocation token, lease, guard, or equivalent
stale-reference fence with these semantics:

```text
StorageEngine generation N publishes service_N
-> service_N may be used only while generation N remains valid
-> generation N disconnect revokes generation N
-> every later service_N invocation fails closed
-> rejection occurs before any new SQLiteDriver.get_session / SQL acquisition
-> a later StorageEngine start may publish service_N+1
-> service_N remains permanently stale and can never become usable again
```

A simple reusable `_started` boolean is insufficient if restarting the same
`StorageEngine` could make a retained service from an older generation usable
again. The fence must distinguish generations (or provide an equivalent
permanent stale-reference guarantee).

The lifetime check belongs to the future activation/lifecycle boundary. It must
be evaluated before the first lower-layer operation that could acquire a SQL
session for a new service invocation. B6 does not redefine already-landed
B1/B3/B4/H-B2 transaction, failure, or cancellation semantics for work that was
already legitimately in flight before revocation.

The service still owns no independent thread, task, event loop, start method,
stop method, engine, or connection pool. A lifetime fence grants no caller,
trigger, automatic promotion, public/model-callable API, capability, UBQ,
routing, CAS, retrieval, or F6 authority.

This follow-up remains CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION.
Production implementation remains CLOSED and requires a separate independent
production PRE-CLAIM after the corrected B6 contract reaches FINAL, lands, and
exact-main post-merge health is GREEN/GREEN.

## 7. Internal-only first activation

The first production activation, if later released, may register one canonical
service in the existing internal `StorageEngine.services` registry.

Registration is dependency availability only. B6 grants no caller and no
trigger that invokes `.promote(...)`.

No new field on `ApplicationContainer` is released by B6. Existing
`ApplicationContainer.storage` is sufficient to expose the already-owned
`StorageEngine` to trusted internal application code if a later stage
explicitly releases a caller.

## 8. No implicit or automatic promotion

Merely registering the service must not cause automatic promotion from:

- tool completion;
- AgentToolResult COMMITTED transition;
- capability invocation completion;
- provider completion;
- Session close;
- task/branch lifecycle;
- retry or crash recovery;
- event-bus publication;
- application startup or shutdown;
- ContextBuilder assembly.

Automatic promotion remains CLOSED.

## 9. No public or model-callable surface

B6 does not authorize:

- HTTP or FastAPI routes;
- FastAPI dependencies exposing promotion;
- tool/capability registration;
- model-visible capability schemas;
- direct-chat hooks;
- Agent automatic calls;
- client protocol changes;
- new public API methods.

A later public/model-callable path would require its own independent authority
review, including UBQ and Issue #156 overlap.

## 10. No new UBQ or routing identity

B6 creates no:

- `capability_id`;
- `invocation_id`;
- target or implementation identity;
- connection identity;
- quota admission;
- charge/refund event;
- timeout policy;
- retry budget;
- provider/tool execution.

Internal storage-service registration is not a logical tool invocation and must
not be counted as one.

## 11. Caller authority remains B5

A future caller may invoke the service only through the existing B5 surface:

```text
promote(
  source_ref = canonical ContextSourceRef,
  owner_user_id = authenticated/canonical owner,
)
```

Activation must not widen caller input to accept:

- content or content snapshot;
- content digest;
- promotion_authority_id;
- PromotionReservation;
- SourcePromotionProof;
- Memory id;
- reservation state;
- caller-selected repository/session/transaction state.

B1/B3/B4/H-B2 remain the only authorities for those values.

## 12. CAS boundary remains opaque

TOOL_RESPONSE_PAYLOAD content remains exact committed opaque JSON under the
landed CTX source contract.

Embedded values that resemble:

- asset IDs;
- `asset://` URIs;
- provider IDs or URIs;
- filenames or paths;
- object keys;
- base64 media;

are not dereferenced, hydrated, rewritten, stripped, or canonicalized as CAS
objects by B6.

CAS #74 retains asset/provider lifecycle and canonicalization authority.

## 13. Retrieval and later CTX stages remain closed

Internal registration of a promotion service is not:

- `ContextSourceKind.MEMORY`;
- Memory retrieval/search/ranking;
- vector/index/chunk/embedding authority;
- ContextBuilder injection;
- model Working Set visibility;
- F6 Personalization;
- F7 Pins/scoring/dedupe;
- F9 Working Set / ContextSnapshot;
- F10 CompactContext.

No later-stage authority is implied by B6.

## 14. Failure and cancellation authority remains lower-layer owned

B6 adds no:

- retry loop;
- fallback;
- exception normalization;
- error translation;
- healing;
- source repair;
- reservation state repair;
- Memory repair.

Cancellation and failures continue to propagate according to B1/B3/B4/H-B2
semantics.

## 15. Cross-track boundaries

### AE-R12 / Issue #107

AE-R12-G0 is canonical on the B6 baseline and is zero-production
contract/integration/architecture evidence. It does not change successful
AgentToolResult content/identity/lineage/liveness consumed by B1.

Any future R12 change to committed result identity/content, owner lineage,
retention/liveness, or recovery ownership is MATERIAL and requires fresh CTX
audit.

### CAS / Issue #74

CAS-F7-T contract preparation is zero-production and explicitly preserves
committed AgentToolResult.output plus CTX opaque-JSON semantics.

Any future CAS proposal that rewrites committed tool-result bytes or causes CTX
to dereference embedded asset/provider material is MATERIAL.

### UBQ / Issue #147

Current timeout migration does not own CTX internal storage-service activation.

A future public/model-callable promotion capability would become MATERIAL to
UBQ accounting/timeout boundaries and requires a separate audit.

### Agent-only / Issue #156

B6 creates no capability, target, implementation, route, fingerprint, sandbox,
or client/server fallback behavior.

### R11 / Issue #31

R11 remains CLOSED / COMPLETE. B6 registration creates no new live root,
retention pin, or destructive-GC authority.

## 16. Future production PRE-CLAIM — explicitly not released

This contract does not release production implementation.

After B6 contract/evidence reaches independent FINAL, lands canonical, and exact
post-merge main is healthy, a separate independent production PRE-CLAIM may
audit the narrow seam around:

```text
se/src/infrastructure/storage/core/manager.py
+ focused production evidence
```

That future audit must freeze exact files. It must not assume `main.py` or
`ApplicationContainer` changes are needed.

## 17. Exact B6 contract scope

Exactly two NEW files:

1. `docs/context_future/CTX_F5_3I_B6_INTERNAL_ACTIVATION_BOUNDARY_522B543E.md`
2. `se/tests/architecture/test_ctx_f5_3i_b6_internal_activation_boundary.py`

No existing file changes are part of B6 contract CLAIM.

```text
se/src/** delta = ZERO
client delta = ZERO
schema/migration delta = ZERO
runtime/container/API delta = ZERO
production merge authority = NONE
```

## 18. Exit gate

B6 contract/evidence may reach FINAL only when:

- changed files remain exactly the two released files;
- current-main/dependency drift is refreshed;
- exact-head Architecture Linux + Windows is GREEN/GREEN;
- architecture evidence proves the canonical StorageEngine/SQLite/B5 facts;
- no blocking P0/P1/P2 remains;
- independent FINAL is recorded.

Only after canonical landing and post-merge health may a separate production
PRE-CLAIM be considered.

## Non-authority statement

CTX-F5-3I-B6 is a zero-production activation-boundary freeze only.

It does not activate Memory promotion, add a caller, register a public
capability, expose an endpoint, modify StorageEngine, change ApplicationContainer,
alter SQL/session ownership, add automatic promotion, open retrieval/model
visibility, or grant F6 authority.
