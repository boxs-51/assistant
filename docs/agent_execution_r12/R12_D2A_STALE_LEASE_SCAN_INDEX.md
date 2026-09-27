# AE-R12-D2A — Stale-Lease Scan Access-Path Index

## Scope

R12-D2A adds only the durable access path required by the already-canonical
R12-D1 bounded read primitive for expired **owned** `RUNNING` executions.

It does **not** add a scanner service, scheduling, cadence policy, takeover,
`WAITING(RECOVERY)`, reconciliation, recovery activation, or any R12-E+
behavior.

## Canonical index

```text
name    = ix_agent_executions_state_lease_expiry_id
table   = agent_executions
columns = (state, lease_expires_at, id)
unique  = false
```

The same index is declared in SQLAlchemy model metadata and Alembic migration
`24a_r12_stale_lease_scan_index -> 23a_ctx_f5_promotion_reservation`.
Metadata-created schemas and migrated schemas must therefore expose identical
index name, uniqueness, and column order.

`owner_instance_id` is intentionally not part of the index. The durable R12-B
constraint pairs owner and expiry nullability, while the D1 predicate also
requires a bounded expiry range. Adding owner would increase write/storage cost
without being required for the released canonical access pattern.

## Query-plan contract

The index supports both D1 shapes:

First page:

```sql
WHERE state = 'RUNNING'
  AND owner_instance_id IS NOT NULL
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at <= :cutoff
ORDER BY lease_expires_at ASC, id ASC
LIMIT :limit
```

Continuation:

```sql
WHERE state = 'RUNNING'
  AND owner_instance_id IS NOT NULL
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at <= :cutoff
  AND (
    lease_expires_at > :after_expiry
    OR (lease_expires_at = :after_expiry AND id > :after_execution_id)
  )
ORDER BY lease_expires_at ASC, id ASC
LIMIT :limit
```

Evidence must show the intended index in SQLite `EXPLAIN QUERY PLAN`, no
unbounded full-table scan, and no avoidable temporary ORDER BY B-tree for the
released query shapes.

## Migration safety

Upgrade creates only the index. Downgrade drops only the index. Neither
direction changes columns, constraints, execution state, lease owner, lease
expiry, lease generation, semantic revision, checkpoint, budget, or request
payload.

R12-D1 result, ordering, cutoff, and keyset semantics remain unchanged.
