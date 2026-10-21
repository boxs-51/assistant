# Alpha defect and uncertainty ledger — 2026-10-19

**Authority:** current investigation queue from historical assessment `76842cf56f40faab3083c6de390373ef56fb65f2`, reconciled for scoped S02/S03 against `alpha@300801d67c43d702408d2895f7c89898e27a87fd` (2026-10-21). For all findings, carry exact original security oracle and never close a defect by removing negative coverage.

| Finding | Severity / type | Evidence (pinned paths, CI) | Owner / tracker | State and next proof |
|---|---|---|---|---|
| F-001 | P0 historical Alembic dual head, S02 scoped fix | Original 31t+31b parallel ancestry; forward-only merge-only `32a_alpha_31b_31t_merge` joins both via [PR #667](https://github.com/boxs-51/assistant/pull/667); 32b follows | #649, #659, #655 | **SCOPED PASS/MERGED_ALPHA** S02 PG17/SQLite recovery; production installed-data upgrade remains unverified |
| F-002 | P1 exact-merge-head integrated CI gap | [PR #668](https://github.com/boxs-51/assistant/pull/668) PR-head 12-workflow green; [merge-SHA AIC467 #39535525959](https://github.com/boxs-51/assistant/actions/runs/39535525959) PG17 Linux/Windows each 58 PASS | #655, #665 | **OPEN**: full S03/Architecture/Redis/OS matrix NOT_RUN at merge SHA; scoped PG17 does not prove full E2E |
| F-003 | P2 stale documentation | README and current-status documents described unresolved two Alembic heads after 32a/32b alpha merge | #656 | **DOCS RECONCILIATION** in alpha post-S03 documentation PR; retain historical snapshots and real-data cautions |
| F-004 | P1 unassessed integration | 9 legacy PRs, 147 path appearances/111 equal/36 different as of pre-merge alpha; docs/alpha-consolidation-and-review-20261019.md | #655 | NOT 36 unique defects; hunk review and custody before any old blob reuse |
| F-005 | P0 account/owner lifecycle, S03 scoped fix | [PR #668](https://github.com/boxs-51/assistant/pull/668) 32b owner witnesses and CAS guards; exact PR-head SQLite 85/Linux + 85/Windows, PG17 9, composite PASS | #649, #660, #655 | **SCOPED PASS/MERGED_ALPHA** S03; parent security, exact merge-SHA full CI and production claims remain OPEN |
| F-006 | P0 inherited OTP semantics | #647 R-ACK tests passed on isolated SHA; unknown Redis ACK and later instance are critical | #650 | UNVERIFIED HA/TTL/reissue and integrated alpha physical |
| F-007 | P0 inherited Native | se/src/runtimes/agent/native_receipt_verifier.py:67–79 deny with no issuer; se/src/runtimes/capability/native_tree_recovery_b0.py header says nonwired | #651 | SECURITY HOLD positive OS custody and signed origin |
| F-008 | P1 inherited HITL/effect | cl/src/core/capability_dispatcher.py + docs/cl_ui/CL242_AE359_EFFECT_DEADLINE_CONTRACT_NEGATIVE.md | #652 | UNVERIFIED real approval/restart/principal switch, no effect replay |
| F-009 | P1 identity/contract architecture | docs/agent_interconnect, docs/skill_v2, docs/agent_platform with mixed current vs target | #653 | PLANNED and CODE_PRESENT separated; runtime binding UNVERIFIED |

## 2026-10-21 post-S03 alpha checkpoint
- **S02 #659:** CLOSED for scoped physical graph/SQLite/PG17 crash+rollback proof; `32a_alpha_31b_31t_merge` joins 31b/31t without rewriting parent migrations.
- **S03 #660:** CLOSED for scoped nonproduction physical proof; `32b_alpha_s03_owner_integrity` is forward-only and follows 32a. Codex automatic **code review** at PR head `a7b4a5c8` found no issues; a Codex **security review** was not run.
- **Post-merge:** `alpha@300801d6` AIC467 PG17 Linux 58 PASS and Windows 58 PASS, but entire S03/Architecture full suite was **NOT rerun** on merge SHA. Preserve gap in #655 / #665.
- **Branch cleanup:** [BRANCH_CLEANUP_AUDIT_20261021.md](BRANCH_CLEANUP_AUDIT_20261021.md) records ancestry/PR evidence; unique unmerged work cannot be deleted merely based on naming convention.

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

## Scoped execution issues (current, not merely proposed)
- [#657](https://github.com/boxs-51/assistant/issues/657): linear coordination.
- [#658](https://github.com/boxs-51/assistant/issues/658): F-002/F-004, exact alpha regression + 36 semantic-drift appearances, S01.
- [#659](https://github.com/boxs-51/assistant/issues/659): F-001, Alembic branch DAG, S02.
- [#660](https://github.com/boxs-51/assistant/issues/660): F-005, account-owner lifecycle, S03.
- [#661](https://github.com/boxs-51/assistant/issues/661): F-006, OTP/refresh/logout, S04.
- [#662](https://github.com/boxs-51/assistant/issues/662): F-007, Native OS issuer/root, S05.
- [#663](https://github.com/boxs-51/assistant/issues/663): F-008, CL/HITL, S06.
- [#664](https://github.com/boxs-51/assistant/issues/664): F-009, identity/Skill/API, S07.
- [#665](https://github.com/boxs-51/assistant/issues/665): integrated end-to-end exit, S09.
No issue creation is a source fix. S01 #658 closed for evidence inventory only, S02 #659 and S03 #660 closed for scoped alpha development; parent #649–#656 and downstream stages retain separate unresolved obligations.
