# S04 portable discovery handoff, exact-head test/CI only

**Finding:** On PR #671 HEAD `83f842f1` Architecture Linux and S03 portable each
collected `test_alpha_s04_acl_restart_sentinel_physical.py`, which requires
`S04_ACL_RESTART_STRICT`/`S04_SENTINEL_STRICT`. Their 8 tests failed with
`S04_HARNESS_ERROR`, not OTP semantic RED. The S03 composite correctly denied
admission because its portable dependency failed.

**Change:** the two portable workflows each add an exact `--ignore-glob` for that
one physical test module. Original 4 `--ignore`, 8 `--deselect`, frozen 62
skip identities, 164 subtests, source pin guards, raw-JUnit check and
`s03_dual_lane_gate.py` remain unchanged.

**New positive and mutation-negative guards:**
`se/tests/unit/test_alpha_s04_acl_portable_isolation_guard.py` checks
portable routing in each lane and asserts removing the new handoff is RED.
It also verifies the two *unchanged* unfiltered physical workflows are
strict, exact-SHA, require all four scenario IDs and zero failed/skipped
testcases. This does not authorize a skip or xfail in any physical test.

**Proven before patch:** `39759440254` Architecture Linux FAIL,
`39759440353` S03 portable FAIL with 8 harness errors and S03 composite
FAIL_CLOSED, while dedicated Redis ACL/Sentinel physical lanes PASS.
Mixed-authority workflow `39759440312` holds **six immutable SEMANTIC_RED**
and four positive controls PASS. Any later green in Architecture/S03 remains
a *scoped routing repair*, not S04 admission.

No production changes, no merge/deploy/independent review.
