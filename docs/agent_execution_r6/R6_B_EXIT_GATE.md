# R6-B Live Remote Outcome / Safe Fallback Exit Gate

**Baseline:** `6770ab09badf4c3f7ab3ccb0abab1bd065047eb3`

**Dependency:** R6-A COMPLETE

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

## Scope

```text
B1 live RemoteOutcomeState CAS transitions
B2 send-attempt transport boundary normalization
B3 NOT_DISPATCHED safe reroute
B4 OUTCOME_UNKNOWN fail-safe classification
B5 IDEMPOTENT / DEDUPLICATED controlled same-invocation fallback
B6 NON_IDEMPOTENT / UNKNOWN fallback block
B7 server availability removed as replay-safety authority
B8 client idempotency declaration propagated during registration
```

## Frozen behavior

```text
remote selected
    -> NOT_DISPATCHED

dispatch attempt begins
    -> IN_FLIGHT

transport proves no send
    -> NOT_DISPATCHED

connection/send lost after dispatch may have begun
    -> OUTCOME_UNKNOWN

remote result/error committed
    -> TERMINAL_COMMITTED
```

Automatic alternate-implementation dispatch:

```text
NOT_DISPATCHED
    -> allowed for every idempotency class

OUTCOME_UNKNOWN + IDEMPOTENT
    -> controlled same-invocation fallback allowed

OUTCOME_UNKNOWN + DEDUPLICATED
    -> controlled same-invocation fallback allowed

OUTCOME_UNKNOWN + NON_IDEMPOTENT
    -> blocked

OUTCOME_UNKNOWN + UNKNOWN
    -> blocked
```

The logical `invocation_id` never changes across an allowed fallback.

## Agent continuation boundary

R6-B removes:

```text
server implementation exists
    == replay is safe
```

If CapabilityRuntime cannot safely resolve the remote invocation, AgentRuntime
checkpoints:

```text
WAITING(CONNECTION)
```

R6-C/R7 later decide reconciliation/resume.

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py `
  se/tests/architecture/test_phase6_3_realtime_multiplex.py `
  se/tests/architecture/test_phase6_8_resilience.py `
  se/tests/architecture/test_phase6_9_continuation.py `
  se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py
```

## R5/R6 regression gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_a_domain_persistence_contracts.py `
  se/tests/integration/test_r6_a_remote_reconciliation_migration.py `
  se/tests/architecture/test_r5_d_agent_delegation_boundary.py `
  se/tests/integration/test_r5_e_task_cancellation_cas.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore d:\assistant\cl\tests\test_mcp_client_ownership.py
```

## Completion evidence

```text
Patch check: <pending>
Focused:     <pending>
Broad:       <pending>
```

## Phase status

```text
R5      COMPLETE
R6-A    COMPLETE
R6-B    IMPLEMENTED / PENDING TEST EVIDENCE
R6-C    PENDING
R6-D    PENDING
R6-E    PENDING
R6      NOT COMPLETE
```
