# R6-A Domain / Persistence Contracts Exit Gate

**Baseline:** `6b3ff6bb0edfedb985401fa86ea7a9924e3cf73d`

**Status:** COMPLETE

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
Patch check:
    PASS
    all 13 R6-A patch actions validated before apply

Focused R6-A gate:
    12 passed, 2 warnings in 12.21s

Initial repository-wide gate:
    608 passed, 1 failed, 9 warnings in 117.44s

The only failure was:
    test_phase6_10_1_true_websocket_remote_agent_tool_loop

The remote WebSocket/tool/second-inference path itself completed successfully.
The failure was the temporal-context assertion:

    second_request.messages[0].content
        !=
    first_request.messages[0].content

Both prompt timestamps were equal because the test depended on host wall-clock
resolution.  This was unrelated to R6-A domain/persistence behavior.

The E2E was corrected to inject a deterministic advancing
TemporalContextProvider rather than depending on two datetime.now() calls
being observably different.

Post-fix E2E evidence:
    run 1: 2 passed in 6.56s
    run 2: 2 passed in 5.03s
    run 3: 2 passed in 5.27s
    run 4: 2 passed in 6.22s

No R6-A production/domain/persistence regression was observed.

No full repository-wide rerun after the deterministic temporal-test fix has
yet been reported.  The R6-B broad exit gate must re-run the complete declared
repository scope and therefore provides the next full-suite confirmation.
```

## Phase status

```text
R5   COMPLETE
R6-A COMPLETE
R6-B ACTIVE
R6-C PENDING
R6-D PENDING
R6-E PENDING
R6 overall NOT COMPLETE
```
