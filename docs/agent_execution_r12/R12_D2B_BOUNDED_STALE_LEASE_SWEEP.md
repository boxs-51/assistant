# AE-R12-D2B — Bounded Stale-Lease Observation Sweep

## Authority

R12-D2B coordinates bounded reads over the canonical R12-D1
`DurableAgentStore.list_expired_execution_leases(...)` primitive.

It is **observation only**. A returned observation is not lease-takeover,
recovery, resume, checkpoint, TaskBudget, or state-transition authority.

Production authority is exactly:

```text
se/src/runtimes/agent/stale_lease_scanner.py
```

There is no schema/model/migration/repository/persistence/runtime/container
change in this stage.

## Sweep semantics

Each `scan_once()`:

1. serializes per coordinator instance;
2. captures one aware UTC `scan_cutoff_utc`;
3. captures one process-local monotonic start;
4. traverses only D1 pages using the same cutoff;
5. advances only with the exact keyset cursor
   `(lease_expires_at, execution_id)`;
6. bounds each page to `1..100`;
7. bounds total work by `max_pages`, `max_rows`, and a positive finite
   `max_duration_seconds`;
8. converts durable rows into frozen observation snapshots;
9. returns a frozen result with explicit stop reason.

Supported stop reasons are:

```text
EXHAUSTED
MAX_PAGES
MAX_ROWS
MAX_DURATION
```

Wall-clock UTC is used only for the durable stale predicate. Monotonic time is
used only for local elapsed-work budgeting.

## Fixed-cutoff rule

The wall clock is read once after a caller acquires the coordinator sweep lock.
Later pages reuse that exact cutoff even if wall clock time advances. Rows that
expire after that cutoff are deferred to a later sweep.

## Backpressure and locking

At most one sweep is active per coordinator instance. Concurrent callers queue
on an `asyncio.Lock`. Because every sweep has finite row/page/time bounds,
that serialization is bounded by the configured sweep work envelope.

The coordinator itself never holds a database row lock. Each D1 page call owns
its short existing unit-of-work boundary and returns before the next page is
requested.

## Safety fences

D2B does not:

- mutate owner, expiry, generation, revision, state, checkpoint, transcript, or
  TaskBudget;
- classify unowned RUNNING as stale;
- infer staleness from process-local supervisor absence;
- release or take over leases;
- transition RUNNING to WAITING(RECOVERY);
- reconcile pending invocations;
- activate recovery/resume;
- create a periodic/startup/background task;
- call `asyncio.create_task()`.

Duplicate observations across separate sweeps are expected and safe. A later
recovery stage must independently and atomically revalidate durable authority.

## Downstream gate

R12-E+ remains closed. A future activation stage may schedule this coordinator
only after a recovery consumer and its lifecycle/shutdown ownership have
independent authority.
