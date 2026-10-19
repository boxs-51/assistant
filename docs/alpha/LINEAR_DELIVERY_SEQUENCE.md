# Alpha serial development contract (S00–S09)

**Goal:** a traceable order for diagnosis, repair, integration and future capabilities from alpha, one bounded successor at a time, no implicit permission widening. **Planning baseline** alpha@76842cf56f40faab3083c6de390373ef56fb65f2 (pre-doc normalization). ALWAYS pin a fresh alpha SHA when a stage is selected.

## Global protocol
Each stage uses: freeze REQ + acceptance → exact HEAD and dependency check → independently sourced RED baseline (bugfix) → smallest authorized source/test/CI change on stage-specific development branch → targeted physical/equivalence regression → independent review before main candidate → document/evidence reconciliation → gate PASS/REVISE/STOP. No skipped security negative assertions. Stage CLOSED only on evidence-backed PASS; otherwise HOLD with preserved issue/oracle. A successful *development* stage does not grant release permission.

Never make old PR unmerged blobs an automatic source of truth. A dirty/stale comparison is an investigation, not an approval to overwrite current code.

| Seq | Stage / tracker | Bounded output | PASS gate / hard STOP |
|---|---|---|---|
| S00 | Canonical docs and issue custody, #656 | source-linked architecture/README/ledger, single ordered plan and issue successors | Document links and expected source SHA checked; 0 source/test changes. |
| S01 | Baseline evidence & divergent-blob audit, #655 | exact-current-alpha CI/coverage runbook; 36 path-appearance hunk custody; RED taxonomy | Each path mapped: SAME/NEWER_ALPHA/MISSING_SEMANTIC/UNVERIFIED with citations; CI jobs classified, no false PASS. |
| S02 | Alembic 31t vs 31b graph, #649/#655 | narrowly scoped migration reconciliation design + regression | Both histories retained; no stamp/drop; fresh populated SQLite + PG17 upgrade, interruption/rollback, version-head CLI and negative oracle; STOP until graph safe. |
| S03 | Account-owner lifecycle (AIC/CAS/UBQ/CTX), #649 | owner REKEY/REPLACE/retention and immutable storage fixes only where proven RED | SQLite FK OFF/ON × recursive OFF/ON and PG17 physical, cross-owner rollback/race, nonweakened oracle. |
| S04 | AE359 OTP and refresh/logout, #650 | single issuance authority + unknown ACK, session linearization | Redis/PG17 real cross-instance, TTL/reissue/failover, hard restart, negative/positive, cross-version and one-time-effect claims. |
| S05 | SBX3 Native OS custody/signed origin, #651 | authenticated issuer/tree custody (NOT blanket Native access) | Real OS Linux cgroup-v2 and Windows Job child/descendant, signed origin/replay, root UNKNOWN quarantine and destructive safety; no spoofable receipt. |
| S06 | CL/UI HITL and effect replay, #652 | generation fence + durable one-use effect, client/server compatibility | Real Windows/Linux E2E, principal switch/logout, timeout/unknown effect, no delayed approval, raw JUnit. |
| S07 | AIC/APR/SKV2 and cross-component API, #653 | ownership/adapter/manifest, cross-principal negative checks | Version/identity contracts, JCS/provenance, no agent context/asset visibility escalation; MARM MA-01..14 reconciliation. |
| S08 | Optional future feature design, #654 | bounded web.download → CAS and game workflow design | Consent/ownership/cleanup and separate explicit Owner authorization; skip implementation if not approved. |
| S09 | Integrated end-to-end readiness, #655 and all earlier | exact merged-alpha full regression and decision packet | Full Linux/Windows/PG17/Redis/SQLite/OS physical oracles, rollback, architecture review; any P0/P1/security HOLD keeps release BLOCKED. |

## Why this is linear, not an artificial single global security lock
Stage sequence controls **promotion of changes and delivery claims**, not unrelated read-only design/research. If stage S03 remains blocked, downstream source promotions that consume its schema are HOLD; independent documentation and evidence gathering may continue, explicitly UNVERIFIED and non-promotion. No generic upstream failure is permission to disable all unrelated safe API surfaces or to bypass real per-resource controls.

## Branch discipline
1. S00 documentation is published directly to development-only alpha on Owner request, with no functional changes.
2. File and issue contract for S01 must be frozen after docs commit.
3. Only after S00 is committed and S01 issue exists, create a clean branch from **exact current alpha HEAD**, e.g. dev/alpha-s01-evidence-20261019. A branch name or existence is NOT implementation.
4. S01 is evidence-first and security-oracle-preserving. Stage S02 does not start until S01 has enough proof to scope the migration fix (its own independent positive/negative witness may be collected, not promoted).
5. Subsequent stage branches are created only from the then-current alpha and only when the prior gate is met, rather than creating a stack of 10 speculative branches.
6. Alpha/main merge, deployment, privileged Native, migration of live data, rule changes and branch deletion require separate human/host authorization. Do not assert review independence without a separately attributable reviewer.

## Delivery checklist / ledger fields
stage_id, parent_issue, requirement_contract, exact_base_sha, changed_paths, RED_run, GREEN_run, Linux/Windows/PG17/Redis matrix, evidence tier, negative-oracle identity, doc_delta, reviewer_provenance, task_verdict, subject_verdict, blockers, rollback, next_stage_ready. Allowed gate values PASS/REVISE/STOP/UNVERIFIED; no skipped gate silently becomes PASS.

## Existing evidence and unverified claims
AIC467 exact prior alpha PG17 run 39258636299 succeeded. Prior PR #648 Linux architecture run 39157350888 failed on a different HEAD. Neither is exact normalized-doc alpha full-system PASS. Migrations have two heads, and Native and OTP physical release claims remain HOLD.

## Live issue-to-stage link map (tracking identifiers, not PASS)
| Stage | Issue | Original parent and dependency |
|---|---|---|
| S00 documentation | [#656](https://github.com/boxs-51/assistant/issues/656) and [tracker #657](https://github.com/boxs-51/assistant/issues/657) | documentation-only normalization complete; broader #656 technical follow-ups remain open |
| S01 evidence | [#658](https://github.com/boxs-51/assistant/issues/658) | #655, after S00 |
| S02 Alembic | [#659](https://github.com/boxs-51/assistant/issues/659) | #649, after S01 gate |
| S03 account owner | [#660](https://github.com/boxs-51/assistant/issues/660) | #649, after S02 gate |
| S04 OTP | [#661](https://github.com/boxs-51/assistant/issues/661) | #650, after S03 gate |
| S05 Native | [#662](https://github.com/boxs-51/assistant/issues/662) | #651, after S04 gate |
| S06 CL/HITL | [#663](https://github.com/boxs-51/assistant/issues/663) | #652, after S05 gate |
| S07 identity/Skill | [#664](https://github.com/boxs-51/assistant/issues/664) | #653, after S06 gate |
| S08 optional features | [#654](https://github.com/boxs-51/assistant/issues/654) | proposal only; no source changes without an independent decision |
| S09 integrated E2E | [#665](https://github.com/boxs-51/assistant/issues/665) | #655 + all mandatory S01–S07 gates |

**Release-gate rule:** S01 may discover that S02 diagnosis can proceed but cannot falsely declare S01 full-suite green. Freeze the relevant inherited RED truth and unblock the smallest valid successor only with grounded evidence and stated residual holds.
