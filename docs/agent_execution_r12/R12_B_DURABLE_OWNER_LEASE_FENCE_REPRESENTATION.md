# AE-R12-B — Durable Owner / Lease / Fence Representation

**Primary authority:** Issue #107  
**Parent candidate:** PR #108 @ `6ca8d1a39aef28479b8ae45b5174e4f77a2ead12`  
**Policy:** Issue #85 v2.5  
**Stage class:** PRODUCTION / REPRESENTATION + MIGRATION ONLY  
**Merge authority:** NONE  
**Parent-first integration:** REQUIRED

## 1. Goal

R12-B adds the durable representation required by the R12-A crash-recovery
contract without activating lease ownership at runtime.

The durable execution row gains:

```text
owner_instance_id : nullable string
lease_expires_at  : nullable UTC wall-clock datetime
lease_generation  : non-null integer, legacy/default 0
```

R12-B does not acquire, renew, release, scan, recover, dispatch, or reconcile
anything. Those authorities remain in later R12 stages.

## 2. Representation semantics

### owner_instance_id

- Opaque server/worker-incarnation identity.
- It is not user_id, client_id, connection_id, session_id, or the process-local
  AgentExecutionSupervisor reservation token.
- NULL means no current durable execution owner.

### lease_expires_at

- Durable UTC wall-clock expiry.
- Monotonic-process clock values must never be persisted.
- NULL means no current durable lease.
- Runtime expiry interpretation is not implemented in R12-B.

### lease_generation

- Durable fencing generation.
- Must be non-negative.
- Legacy rows use generation 0.
- A non-null owner requires generation > 0.
- Future lease acquisition must advance generation monotonically.
- Clearing owner/expiry must not imply generation reuse or reset.

Valid representation pairs:

```text
unowned:
  owner_instance_id = NULL
  lease_expires_at  = NULL
  lease_generation  >= 0

future active lease:
  owner_instance_id != NULL
  lease_expires_at  != NULL
  lease_generation  > 0
```

## 3. Database constraints

The SQL model and migration enforce:

```text
owner_instance_id and lease_expires_at are both NULL or both non-NULL
lease_generation >= 0
owner_instance_id IS NULL OR lease_generation > 0
```

No database check interprets current time or decides whether a lease is stale.

R12-D owns stale-RUNNING query/scanner/index policy, so R12-B intentionally adds
no lease-expiry index.

## 4. Migration

```text
revision      = 22a_r12_execution_lease_fence
down_revision = 21a_ctx_f5_memory_foundation
table         = agent_executions
```

Upgrade behavior:

- adds the three representation columns;
- preserves every existing execution state/revision/checkpoint/budget value;
- legacy rows become owner=NULL / expiry=NULL / generation=0;
- does not infer an owner for historical RUNNING rows;
- does not move any execution to WAITING(RECOVERY);
- does not reconcile any invocation.

Downgrade is fail-closed. It refuses to remove the representation if any row
contains non-default durable lease/fence authority:

```text
owner_instance_id IS NOT NULL
OR lease_expires_at IS NOT NULL
OR lease_generation != 0
```

## 5. Persistence compatibility

Existing AgentRepository primitives are intentionally reused unchanged:

- `save_execution(values)` forwards scalar fields into AgentExecutionRecord;
- `get_execution(execution_id)` loads the durable row;
- `compare_and_set_execution(...)` applies generic scalar values under the
  existing revision CAS.

R12-B therefore does not modify the Agent repository or DurableAgentStore merely
to round-trip owner/lease/fence representation.

## 6. Exact production scope

Owned production files:

```text
se/src/domain/schemas/agent_execution.py
se/src/infrastructure/storage/models/sql/agent/execution.py
se/src/infrastructure/storage/migrations/sql/versions/22a_r12_execution_lease_fence.py
```

Evidence/documentation:

```text
docs/agent_execution_r12/R12_B_DURABLE_OWNER_LEASE_FENCE_REPRESENTATION.md
se/tests/architecture/test_r12_b_execution_lease_representation.py
se/tests/integration/test_r12_b_execution_lease_migration.py
```

Historical migration tests may receive mechanical current-head expectation
updates from 21a to 22a. Those changes do not alter historical lineage assertions
or production authority.

## 7. Authority kept closed

R12-B does not implement:

- lease acquire / renew / release CAS — R12-C;
- active runtime lease/fence checks before provider/tool dispatch — later R12;
- stale-RUNNING query/scanner/index policy — R12-D;
- RUNNING -> WAITING(RECOVERY) takeover — R12-E;
- pending invocation reconciliation / recovery activation — R12-F;
- restart/multi-worker race orchestration — R12-G;
- final fault matrix — R12-H.

R6-R10 side-effect/retry/reconciliation authority and R11
checkpoint/transcript/retention/GC authority remain inherited.

## 8. Material drift rule

R12-B is stacked on exact R12-A parent `6ca8d1a3...`.

Any new Alembic migration landing before R12-B integration changes the required
migration parent and is MATERIAL even if its business semantics are unrelated.
The migration parent must then be refreshed/reclassified before integration.

## 9. Exit gate

R12-B candidate may become FINAL GREEN only when:

1. exact representation and constraints are present in domain + SQL model;
2. migration is linear at 22a -> 21a;
3. legacy upgrade preserves prior durable execution values;
4. generic repository save/load and revision CAS round-trip the new scalars;
5. invalid pairing/negative generation is rejected;
6. downgrade fails closed for non-default authority and succeeds for defaults;
7. no runtime lease behavior is introduced by the production diff;
8. R11 persistence/checkpoint behavior remains green;
9. Linux + Windows full Architecture is GREEN;
10. independent audit finds no blocking P0/P1 and confirms migration-head drift.
