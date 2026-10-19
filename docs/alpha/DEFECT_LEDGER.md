# Alpha defect and uncertainty ledger — 2026-10-19

**Authority:** current investigation queue, not a declaration every candidate is reproduced on the latest docs commit. Base assessment 76842cf56f40faab3083c6de390373ef56fb65f2. For all findings, carry exact original security oracle and never close a defect by removing negative coverage.

| Finding | Severity / type | Evidence (pinned paths, CI) | Owner / tracker | State and next proof |
|---|---|---|---|---|
| F-001 | P0 confirmed schema graph | se/src/infrastructure/storage/migrations/sql/versions/31t_direct_terminal_permission.py:16–18 and 31a...:12–15 both down 30a; 31b...:17–20 down 31a; prior PR #648 run 39157350888 Linux failed multiple-head | #649 and #655 | BLOCKED; inventory effective Alembic heads, then real rollback/upgrade |
| F-002 | P1 CI coverage gap | .github/workflows/architecture-baseline.yml:3–7 push main or PR; run 39258636299 PG17-only success | #655 | UNVERIFIED full alpha; exact SHA Linux/Windows/full physical needed |
| F-003 | P2 docs gap | README.md originally only title; source has cl/src/main.py and se/src/main.py | #656 | DOC NORMALIZATION IN PROGRESS; verify navigability |
| F-004 | P1 unassessed integration | 9 legacy PRs, 147 path appearances/111 equal/36 different as of pre-merge alpha; docs/alpha-consolidation-and-review-20261019.md | #655 | NOT 36 unique defects; hunk review and custody before any old blob reuse |
| F-005 | P0 inherited account/owner | PR #648 full-suite had SQLite FK-OFF identity/REKEY/REPLACE negative failures, not exact-current-alpha | #649 | UNVERIFIED exact tree; physical SQLite/PG17 root/owner matrix |
| F-006 | P0 inherited OTP semantics | #647 R-ACK tests passed on isolated SHA; unknown Redis ACK and later instance are critical | #650 | UNVERIFIED HA/TTL/reissue and integrated alpha physical |
| F-007 | P0 inherited Native | se/src/runtimes/agent/native_receipt_verifier.py:67–79 deny with no issuer; se/src/runtimes/capability/native_tree_recovery_b0.py header says nonwired | #651 | SECURITY HOLD positive OS custody and signed origin |
| F-008 | P1 inherited HITL/effect | cl/src/core/capability_dispatcher.py + docs/cl_ui/CL242_AE359_EFFECT_DEADLINE_CONTRACT_NEGATIVE.md | #652 | UNVERIFIED real approval/restart/principal switch, no effect replay |
| F-009 | P1 identity/contract architecture | docs/agent_interconnect, docs/skill_v2, docs/agent_platform with mixed current vs target | #653 | PLANNED and CODE_PRESENT separated; runtime binding UNVERIFIED |

## How to triage
- SEMANTIC_RED: expected protected invariant breaks in valid physical negative test; never label infrastructure.
- HARNESS_ERROR: test cannot exercise target properly (missing service, wrong strict mode, invalid collected selector).
- INFRA_FAILURE: runner/service failure unrelated to target assertion.
- NOT_RUN / UNVERIFIED: no exact pinned target experiment.
- Historical branch-scoped PASS is not an exact-alpha PASS.
- Every issue must record trigger, touched module, immutable SHA/link, reproducer, acceptance, rollback and negative oracle. Do not duplicate the eight parent issues by opening unbounded adjacent trackers.

## Parent issue references
[#649](https://github.com/boxs-51/assistant/issues/649) · [#650](https://github.com/boxs-51/assistant/issues/650) · [#651](https://github.com/boxs-51/assistant/issues/651) · [#652](https://github.com/boxs-51/assistant/issues/652) · [#653](https://github.com/boxs-51/assistant/issues/653) · [#654](https://github.com/boxs-51/assistant/issues/654) · [#655](https://github.com/boxs-51/assistant/issues/655) · [#656](https://github.com/boxs-51/assistant/issues/656).

No source fixes or full E2E certification occur in this document.
