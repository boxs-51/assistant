# AE-R9-D Completion — DISCARD

**Status:** COMPLETE / GREEN

## Delivered

- Deterministic branch locking.
- Monotonic OPEN -> DISCARDED resolution.
- Exact-once `active_branches` accounting.
- Final-OPEN-branch protection and idempotent replay.

## Evidence

`test_r9_d_discard_is_monotonic_accounted_once_and_keeps_task_open` passes in
`se/tests/integration/test_r9_de_branch_resolution.py`.
