# Phase 5.8 Exit Gate

## Purpose

This document is the canonical release gate for closing Phase 5.8 and opening Phase 5.9.

**Rule:** Phase 5.9 must not start while any E1–E7 criterion is red.

The authoritative executable gate is:

```text
tests/architecture/test_phase5_8_exit_gate.py
```

The gate is deliberately fail-closed. A green local result is necessary but not sufficient for the final decision: the repository CI run for the same commit must also be green.

## E1

### Batch execution is bounded and uses a single concurrency authority

The batch tool path must use one authoritative coordinator and a bounded semaphore or equivalent. Unbounded dispatch must not be used in the production path.

Proof:

```text
test_E1_batch_execution_is_bounded_and_ordered
```

## E2

### Batch results preserve original input ordering

Tool results must be emitted in the same order as the incoming request list, independent of completion timing.

Proof:

```text
test_E2_batch_results_preserve_input_order
```

## E3

### Duplicate or mismatched results are rejected fail-closed

Duplicate `tool_call_id`s, duplicate downstream results, missing results, and mismatched execution metadata must fail before returning a result payload.

Proof:

```text
test_E3_duplicate_and_mismatched_batch_results_are_rejected
```

## E4

### Retry policy respects the retryable signal and budget

Only retryable failures may trigger retry, and total retries must respect `max_retry_attempts` and execution budget rules.

Proof:

```text
test_E4_retry_policy_respects_retryable_signal_and_budget
```

## E5

### Cancellation and shared execution remain safe under batch concurrency

A caller cancellation must not cancel a shared execution while another waiter remains attached, and a completed shared execution must still be committed to the ledger.

Proof:

```text
test_E5_cancelled_waiters_do_not_break_shared_batch_execution
```

## E6

### CI runs the full suite plus the Phase 5.8 gate

The repository must declare a blocking GitHub Actions workflow that runs both the full suite and the Phase 5.8 gate.

Proof:

```text
test_E6_ci_declares_full_suite_and_phase_5_8_gate_as_blocking_checks
```

## E7

### Documentation has one canonical Phase 5.8 status source

The canonical current Phase 5.8 gate document must be this file, and legacy status reports must point to it rather than contradicting the current implementation status.

Proof:

```text
test_E7_phase_5_8_status_document_is_canonical_and_legacy_doc_points_to_it
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
Phase 5.8 CLOSE
        ↓
Phase 5.9 OPEN
```

No individual test may be skipped, xfailed, or marked optional for a release candidate.
