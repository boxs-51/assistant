# S03 Trusted Admission Contract — external authority REQUIRED

**Status:** DESIGN + PR-local hardening only. NOT an approved required check; NOT an external security attestation. Fixed source: PR #668 at c47d580 (predecessor). The protected baseline and this file must not be treated as interchangeable evidence.

## Goal and frozen boundaries

- F01: A repository administrator MUST enforce a GitHub ruleset on `refs/heads/alpha`, requiring the actual S03 trusted admission status plus Architecture Linux/Windows, CAS and S02 physical checks at one PR HEAD. Protect the verifier's workflow/provider identity (GitHub App, not a check name alone) and require approval for changes in the security-oracle paths; stop on missing/pending/skipped/cancelled/stale checks. The observed `alpha` branch is currently `protected=false` and has required checks OFF. An in-PR YAML change **cannot** activate branch protection.
- F02: Trusted verifier code, frozen oracle/test source digests, nine PG17 test identities, 85 SQLite identities per OS, permitted portable SKIP identities, and event/run acceptance policy must reside **outside PR-writable contents** (admin-governed reusable workflow at immutable SHA or external attesting GitHub App). Changes require separate authorization. Self-supplied `approved=true`, blob hash changes, or a PR-controlled validator are NOT proof of approval.
- Protected object: exact `repository_id`, `pull_request.number`, base ref `alpha`, `head_sha`, trusted workflow ID/path and source ref, GitHub `run_id`, `run_attempt`, each expected `job_id`, terminal job conclusions, artifact ID/hash/provenance and trusted checkout identity. Reject artifacts from other repos/PRs/SHA/runs, ambiguous re-runs and any unapproved test or workflow changes.
- F03: Portable JUnit must retain the 62 approved skipped-node identities from physical run #39440483768; a swap at the same skipped count MUST fail. No mandatory security test may be skipped or lost. Subtest count/summary rules and eight explicit PG17 handoffs remain.
- F04: Both SQLite OS runners MUST execute the identical pinned set of 85 tests without skip/fail/error/xfail/xpass, plus the nine strict PG17 tests and portable tests. External admission MUST also require independent Architecture Linux, Windows client/server, historical S02 PG17+two-OS SQLite, and CAS Linux+Windows on that very HEAD. PR-local S03 Composite may verify only its same-workflow dependency jobs; it cannot authorize sibling workflows by itself.
- Do not change Alembic/32b, retained CTX/CAS/UBQ negative assertions, AE359 exactly-three file handoffs, and PostgreSQL 17 no-fallback rules to satisfy CI.

## Authorized staging / negative trials before any alpha admission

1. On a **disposable non-production PR** to `alpha`, confirm required check is absent: merge action is disabled; do not actually merge.
2. Repeat with S03 failed, cancelled, stale SHA, renamed check, unapproved GitHub App identity, and one sibling Architecture/S02/CAS job missing. Every case must block admission.
3. Replay a JUnit ZIP from a prior run with edited `head.txt`, omit or duplicate a physical testcase, swap a portable skip with a passing security testcase, or modify the oracle and its PR-local expected hash. Trusted verifier must reject all.
4. Require authenticated admin read of `alpha` protection/rulesets after configuration. A PR comment, PASS string, or local script cannot prove the setting.
5. Only then request **separate** review of the exact trusted verifier plus latest #668 evidence. Keep PR Draft, issue #660 OPEN and S04 HOLD until authorized. **NO_MERGE / NO_DEPLOY**.

## Implementation split at this PR

The in-PR S03 verifier adds defense-in-depth for F03/F04, including two-OS SQLite raw JUnit and exact SHA artifacts. It does **not** resolve the outside-PR F01/F02 trust boundary. If administration is unavailable, record `F01=BLOCKED_EXTERNAL_PERMISSION`, `F02=BLOCKED_EXTERNAL_TRUST_ANCHOR` and never mark S03 admission PASS.
