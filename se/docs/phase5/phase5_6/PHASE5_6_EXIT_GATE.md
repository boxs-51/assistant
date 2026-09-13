# Phase 5.6 Exit Gate

## Purpose

This document is the canonical release gate for closing Phase 5.6 and
opening Phase 5.7.

**Rule:** Phase 5.7 must not start while any E1–E7 criterion is red.

The authoritative executable gate is:

```text
tests/architecture/test_phase5_6_exit_gate.py
```

The gate is deliberately fail-closed. A green local result is necessary but
not sufficient for E5: the GitHub Actions run for the same commit must also be
green.

## E1

### Caller cancellation != shared execution cancellation

Cancelling one waiter must detach only that waiter. The underlying shared
execution remains alive while another waiter is attached.

Proof:

```text
test_E1_caller_cancellation_detaches_waiter_but_shared_execution_continues
```

## E2

### Execution cancellation cancels the shared task exactly once

Execution-level cancellation must cancel the shared downstream execution,
while every waiter observes terminal cancellation. The downstream executor
must observe one cancellation for one shared invocation.

Proof:

```text
test_E2_execution_cancellation_cancels_shared_task_once_for_all_waiters
```

## E3

### Completed shared execution is committed to the idempotency ledger

If the shared downstream task has completed before a final waiter cancellation
is observed, the completed result must still become reusable state.

Proof:

```text
test_E3_completed_shared_execution_is_committed_even_when_last_waiter_cancels
```

## E4

### No duplicate redispatch after completion/cancellation race

A new caller using the same `execution_id + invocation_id` after the race must
receive the original result without executing the downstream side effect again.

Proof:

```text
test_E4_completion_or_cancellation_race_never_redispatches_same_invocation
```

## E5

### Full test suite + CI are mandatory gates

The repository must have a blocking GitHub Actions workflow which runs:

```text
python -m pytest -q
python -m pytest -q tests/architecture/test_phase5_6_exit_gate.py
```

The E5 test verifies the workflow declaration. The final release decision
requires the workflow run for the candidate commit to be green.

Proof:

```text
test_E5_ci_declares_full_suite_and_exit_gate_as_blocking_checks
```

## E6

### Completed-ledger retention is explicitly bounded

The process-local completed idempotency ledger must have an explicit bounded
retention policy. It must not grow indefinitely with gateway lifetime.

Proof:

```text
test_E6_completed_ledger_has_bounded_retention
```

## E7

### Documentation has one canonical current-status source

`PHASE5_6_EXIT_GATE.md` is the canonical current Phase 5.6 gate. Legacy
status documents must point readers to this file rather than presenting stale
Phase 5.6 implementation claims as current truth.

Proof:

```text
test_E7_phase_5_6_status_document_is_canonical_and_legacy_status_declares_it
```

## Release decision

```text
E1  PASS
E2  PASS
E3  PASS
E4  PASS
E5  PASS + green CI
E6  PASS
E7  PASS
----------------
Phase 5.6 CLOSE
        ↓
Phase 5.7 OPEN
```

No individual test may be skipped, xfailed, or marked optional for a release
candidate.