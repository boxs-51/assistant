# AE-R12-D1 — Bounded Expired-Owned RUNNING Observation

**Primary authority:** Issue #107 / AE-R12  
**Parent:** PR #120 @ `b4234a5db79f09dbe50d591b1346293132640f44`  
**Policy:** Issue #85 v2.5  
**Stage:** R12-D1 / STACKED DEVELOPMENT DEPTH 2  
**Merge authority:** NONE

## Goal

R12-D1 adds a dormant, bounded, read-only persistence primitive for observing
expired **owned** durable RUNNING executions. An observation is evidence for a
future R12-E atomic takeover recheck; it is never takeover authority itself.

## Query contract

```text
state == RUNNING
owner_instance_id IS NOT NULL
lease_expires_at IS NOT NULL
lease_expires_at <= cutoff_utc
ORDER BY lease_expires_at ASC, id ASC
LIMIT finite_limit
```

The caller supplies one timezone-aware UTC `cutoff_utc`. A future scanner
coordinator must reuse one fixed cutoff for a bounded sweep. D1 does not capture
wall-clock time or schedule repeated scans.

Keyset pagination uses the complete cursor:

```text
(after_expiry, after_execution_id)
```

The next page contains rows for which:

```text
lease_expires_at > after_expiry
OR (
  lease_expires_at == after_expiry
  AND id > after_execution_id
)
```

Both cursor fields must be supplied together. UTC/cursor/limit validation fails
closed. The hard page maximum is 100.

## Result snapshot

Each returned durable record preserves the exact observed:

- execution id;
- state;
- owner_instance_id;
- lease_generation;
- lease_expires_at;
- semantic revision and other durable fields for diagnostics.

The result is read-only. D1 does not mutate any execution field.

## Critical classification boundary

`UNOWNED RUNNING != STALE`.

A RUNNING execution with:

```text
owner_instance_id = NULL
lease_expires_at = NULL
```

is never returned by D1. R12-B migration can produce this representation for
legacy RUNNING rows and R12-C uses it as fresh-acquire eligibility.

## Explicitly closed

R12-D1 does **not** add or activate:

- a periodic/background/startup scanner;
- an Alembic migration or scanner index;
- expired-owner takeover;
- lease owner/generation mutation;
- RUNNING -> WAITING(RECOVERY);
- recovery ownership publication;
- provider/remote invocation reconciliation;
- AgentRuntime or supervisor activation;
- semantic transition fencing;
- R12-E/F/G/H behavior.

Canonical 22a has no purpose-built `(state, lease_expires_at, id)` index, so
this primitive must remain dormant/bounded in D1. Any scheduled scanner/index is
a separately released R12-D2 stage and must resolve migration lineage from the
then-current canonical head.

## Race boundary

Duplicate observations are allowed. Release, release+reacquire, semantic state
movement, or another recovery worker may make a scan receipt stale. Future R12-E
must atomically revalidate exact RUNNING + owner + generation + expiry and
provide the single mutation winner.

## Exit evidence

D1 requires evidence that:

- expired owned RUNNING rows are returned;
- active owned rows are excluded;
- unowned RUNNING rows are excluded;
- WAITING and terminal rows are excluded;
- expiry equal to cutoff is included;
- ordering is deterministic including equal-expiry id tie-break;
- keyset traversal has no duplicates;
- invalid UTC/cursor/limit inputs fail closed;
- observation leaves durable state unchanged;
- exact-head Linux + Windows Architecture is GREEN;
- independent audit finds no blocking R12-D1 P0/P1/P2.
