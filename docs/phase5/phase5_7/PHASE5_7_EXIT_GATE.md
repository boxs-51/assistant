# Phase 5.7 Exit Gate

## Purpose

This document is the canonical release gate for closing Phase 5.7 and
opening Phase 5.8.

**Rule:** Phase 5.8 must not start while any E1–E7 criterion is red.

The authoritative executable gate is:

```text
tests/architecture/test_phase5_7_exit_gate.py
```

The gate is deliberately fail-closed. A green local result is necessary but
not sufficient for the final decision: the repository CI run for the same
commit must also be green.

## E1

### Invalid tool arguments are rejected before downstream dispatch

The canonical agent tool boundary validates JSON Schema before a capability is
invoked. Malformed or type-invalid arguments must fail with
`CAPABILITY_INVALID_ARGUMENT` and never reach the capability driver.

Proof:

```text
test_E1_invalid_arguments_are_rejected_before_capability_dispatch
```

## E2

### Invalid capability schemas fail closed

A malformed capability schema must be rejected as a configuration error, not
accepted into the registry and later failed at execution time.

Proof:

```text
test_E2_invalid_capability_schema_fails_closed
```

## E3

### Visibility and authorization checks are explicit and agent-scoped

A tool path must separate:
- global registry existence/executability
- agent-specific visibility
- authorization
- argument validation

Proof:

```text
test_E3_visible_and_authorized_capability_path_remains_separate_from_legacy_registry_visibility
```

## E4

### Canonical downstream capability errors preserve code and retryability

When a capability driver raises `CapabilityError`, the wrapper must preserve the
canonical error code and retryability signal instead of rewrapping it under a
new class or new semantics.

Proof:

```text
test_E4_capability_error_codes_and_retryability_are_preserved_verbatim
```

## E5

### Full test suite + gate are mandatory blocking checks

CI must run:

```text
python -m pytest -q
python -m pytest -q tests/architecture/test_phase5_7_exit_gate.py
```

Proof:

```text
test_E5_ci_declares_full_suite_and_phase_5_7_exit_gate_as_blocking_checks
```

## E6

### The canonical Phase 5.7 gate is the single source of truth

The canonical Phase 5.7 gate document must be this file, and legacy status docs
must point to it instead of presenting stale or contradictory implementation
status as current truth.

Proof:

```text
test_E6_phase_5_7_status_document_is_canonical_and_legacy_doc_points_to_it
```

## E7

### Tool error contract remains canonical and non-retryable validation failures stay non-retryable

The public tool error contract must include the canonical error codes for tool
visibility, invalid arguments, timeout, cancellation, and execution failures.
Validation failures are not retryable.

Proof:

```text
test_E7_tool_error_contract_is_canonical_and_non_retryable_invalid_arguments_stay_non_retryable
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
Phase 5.7 CLOSE
        ↓
Phase 5.8 OPEN
```

No individual test may be skipped, xfailed, or marked optional for a release
candidate.
