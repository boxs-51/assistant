# R6-A Domain / Persistence Contracts Exit Gate

**Baseline:** `6b3ff6bb0edfedb985401fa86ea7a9924e3cf73d`

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

## Scope

```text
A1 CapabilityIdempotency contract
A2 RemoteOutcomeState contract
A3 semantic request fingerprint
A4 stable R6 reconciliation error codes
A5 durable CapabilityInvocation reconciliation snapshot
A6 SQL read + revision/CAS round-trip
A7 migration 12a_r6_remote_reconciliation
```

## Frozen invariants

1. Legacy capability idempotency defaults to `UNKNOWN`.
2. Remote outcome certainty is separate from invocation lifecycle state.
3. Historical rows are never backfilled with fake delivery certainty.
4. Historical client/principal provenance remains NULL when unknown.
5. One semantic request fingerprint contains capability ID, version and
   arguments using canonical JSON + SHA-256.
6. `invocation_id` remains the logical invocation identity; the fingerprint
   detects conflicting reuse of that identity.
7. Existing CapabilityInvocation revision/CAS remains the durable server
   mutation fence.
8. R6-A does not change disconnect fallback or remote dispatch behavior.
9. R6-B owns live `RemoteOutcomeState` transitions.
10. R6-C owns reconciliation wire protocol.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_a_domain_persistence_contracts.py `
  se/tests/integration/test_r6_a_remote_reconciliation_migration.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py
```

## R5/R6 regression gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_capability_invocation_lifecycle.py `
  se/tests/architecture/test_r5_d_agent_delegation_boundary.py `
  se/tests/integration/test_r5_e_task_cancellation_cas.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

## Completion evidence

```text
Patch check: <pending>
Focused:     <pending>
Broad:       <pending>
```

## Phase status

```text
R5   COMPLETE
R6-A IMPLEMENTED / PENDING TEST EVIDENCE
R6-B PENDING
R6-C PENDING
R6-D PENDING
R6-E PENDING
R6 overall NOT COMPLETE
```
