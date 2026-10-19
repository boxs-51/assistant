# ALPHA consolidation and architecture/error review — 2026-10-19

**Document ID:** ALPHA-REV-20261019-R1  
**Version:** 1.0 / administrative audit; **status:** preliminary, unresolved technical gates  
**Owner:** Human repository Owner; **author role:** repository integration/review assistant  
**Environment:** non-production `alpha`; **main untouched**  
**Review evidence pin (prior to this documentation commit):** `alpha@87b830781f5d7ad0e1a2e5e143b6c510bfa89052`; `main@858004fae17732f3b388383c28146c12b36ecdb9`.  
**Historical baseline:** [alpha ledger 2026-10-18](alpha-integration-ledger-20261018.md). Do not reinterpret that ledger's historical numbers as current completion.

## 1. Actual integration performed, NOT bug remediation

- Saved pre-update alpha at branch `archive/alpha-pre-consolidation-20261019@33135f4d0d4f2129189b1fd462cadfb00ec61805`.
- Composed exactly 13 already-authored source/test/workflow blobs from [PR #647](https://github.com/boxs-51/assistant/pull/647) at `215034b0b80e654d5ad7f2b0622bdf733ad440bd` (5 paths) and [PR #648](https://github.com/boxs-51/assistant/pull/648) at `9cbaa9d4fe69facdc51fa40557162bff15c59e01` (8 paths). Verified those path sets have no overlap and each unchanged file in one branch matches old alpha in the other.
- Three-parent merge commit `87b830781f5d7ad0e1a2e5e143b6c510bfa89052`, parents = old alpha, #647, #648; merged tree `019d7416478dfcb9a56ceb3358d80bf6dc42f814`. No changes to existing security assertions were authored during consolidation.
- Development assembly/ancestry is not a production activation or a blanket security PASS. **NO_MAIN_MERGE, NO_DEPLOY, SECURITY_HOLD.**

## 2. Remaining open legacy PR inventory at review pin

| PR | Source | Paths on that PR | SHA-identical with old alpha | Divergent paths | Disposition |
|---|---|---:|---:|---:|---|
| #627 | #465 Native D2 | 10 | 3 | 7 | Archive; semantic conflict |
| #623 | #487 Native revocation | 5 | 3 | 2 | Archive; semantic conflict |
| #614 | #549 AE359 | 25 | 19 | 6 | Archive; semantic conflict |
| #613 | #555 DIRECT | 6 | 4 | 2 | Archive; semantic conflict |
| #612 | #579 OTP physical | 37 | 33 | 4 | Archive; semantic conflict |
| #609 | #583 ingress | 22 | 17 | 5 | Archive; semantic conflict |
| #563 | Redis uncertainty | 13 | 11 | 2 | Archive; semantic conflict |
| #554 | Main-target OTP composition | 26 | 20 | 6 | Archive; semantic conflict |
| #494 | PG17 Alembic version width | 3 | 1 | 2 | Archive; semantic conflict |
| **TOTAL** | | **147** | **111** | **36** | **0 missing/new paths** |

**Important qualification:** a divergent blob can represent newer alpha behavior, distinct implementation, incomplete inclusion or a real lost change. It cannot be labelled `already integrated` or `discardable` from file SHA alone. Do not auto-merge stale entire files or count archiving as issue resolution. All source commits and discussions remain retrievable in GitHub history. Verify hunk-level and behavioral ownership before any future recovery.

## 3. Observed CI/errors and interpretation

- Independent **#647** Redis RESP/PG17 physical R-ACK branch: [strict 5-case run](https://github.com/boxs-51/assistant/actions/runs/39150765774) PASS; [post-ACK run](https://github.com/boxs-51/assistant/actions/runs/39150765793) PASS 9 tests. This is positive evidence for that exact branch, NOT complete alpha after integration or HA/TTL/failover release.
- Independent **#648** Native D2 root-custody [Linux/Windows 15-case run](https://github.com/boxs-51/assistant/actions/runs/39157350882) PASS; [alias test](https://github.com/boxs-51/assistant/actions/runs/39157350879) PASS. OS-owned positive native custody is still unverified.
- Separate #648 branch still exhibited R-ACK-07 **1 SEMANTIC_RED / 8 PASS** [run](https://github.com/boxs-51/assistant/actions/runs/39157350977), because it did not contain #647; the new combined tree needs its own physical run.
- #648 branch [Architecture Baseline](https://github.com/boxs-51/assistant/actions/runs/39157350888) `linux-full-suite` **160 FAIL / 4278 PASS / 87 SKIP / 10 ERROR**. Traces include multiple Alembic revision heads; SQLite FK-OFF identity/REKEY/REPLACE owner mutation accepted; missing strict PG17 service admission in a different full-suite harness. Classify separately: schema/owner cases **SEMANTIC_RED**, strict service admission **HARNESS_ERROR**, never convert to PASS. These are **not exact combined-alpha measurements**.
- `.github/workflows/architecture-baseline.yml` triggers on `pull_request` or push to `main`, **not on plain alpha push**. At authoring, no exact combined-alpha Linux+Windows full-suite certification had been established. Redis/PG17, SQLite matrix and real OS kernel gates still require independent evidence.

## 4. Architecture review — current state vs proposed verification

Current physical/semantic boundary evidenced by inspected code, docs and test files: `cl` client and `se` server capability runtime; Agent/native receipt verifier and sandbox; cache Redis + SQL persistence; Alembic migrations; CAS/UBQ/CTX user-owned data and SKV2/Agent contracts. Legacy and modern paths coexist. A green narrow regression on one path must not authorize an alternate path.

| Review axis | Finding / proposed gate | Status |
|---|---|---|
| MA-01..03 scope, interface, ownership | Agent native caller-origin receipt vs trusted DIRECT; durable owner of one OTP issuance; client/SE role separation | REVISE: formal end-to-end data-flow/ADR needed |
| MA-04..06 shared DB, coexistence, dual-write | Redis SET ACK-loss vs independent PG fallback; AIC/UBQ/CAS/CTX legacy SQL FK-off modes | HOLD: physical semantics, migration graph |
| MA-07..09 migration, rollback, adapter | Alembic multiple heads; PG17/SQLite populated upgrade; no undocumented default `head` routing | HOLD: canonical DAG and rollback |
| MA-10..12 parallel-run, cutover, identity | Old client/new server; fresh RedisDriver after unknown ACK; signed origin, no authentication widening | HOLD: mixed-client and real OS proofs |
| MA-13..14 testing and intentional hybrid | Positive test cannot supersede negative oracle; native OS quarantined UNKNOWN; no forced legacy rewrites solely for style | HOLD pending exact-tree E2E and independent review |

**Proposed architecture decisions are NOT approved implementations.** Source access rules, owner identity, crash-safe unknown status, replay fences, root custody, and rollback are hard independent gates. Preserve intentional hybrid systems where functioning; fix only on new scoped branches after RED-first diagnosis.

## 5. Documentation review

1. Root [README](../README.md) contains effectively only `# assistant`: missing quickstart, `cl`/`se` layout, dependency setup, alpha-vs-main semantics, testing/environment prerequisites and active architecture/issue links. This is a documentation gap, not an operational readiness finding.
2. [Old alpha integration ledger](alpha-integration-ledger-20261018.md) is a **historical snapshot**; its “do not close issues” was the Owner's earlier instruction, superseded for **administrative archiving only** by 2026-10-19 Owner request. Historical material and RED gates remain unchanged.
3. [Roadmap namespace registry](ROADMAP_NAMESPACE_REGISTRY.md) already states old Policy #85/M1–M7 are retired/non-normative. Some internal sections still use historical active-sounding text; reconcile references prospectively, do not reactivate policy.
4. Technical issue #560 Owner independent-review exception remains an **active Owner decision** limited to reviewer procedure, NOT permission to bypass GitHub/security/physical gates. Closing its historical issue record does not revoke it. The successor issue captures continuity.

## 6. New issue inventory and original-to-successor map

| New issue | Subject | Historical issues administratively superseded |
|---|---|---|
| [#649](https://github.com/boxs-51/assistant/issues/649) | P0 Account/migration integrity | #589 #426 #381 #141 #149 #74 #15 |
| [#650](https://github.com/boxs-51/assistant/issues/650) | P0 OTP / refresh/logout and timeout | #592 #587 #359 #147 |
| [#651](https://github.com/boxs-51/assistant/issues/651) | P0 Native/C2/D2 / signed origin | #591 #585 #584 #417 #164 #371 #167 |
| [#652](https://github.com/boxs-51/assistant/issues/652) | P1 HITL / client / Windows reliability | #588 #242 #399 #416 |
| [#653](https://github.com/boxs-51/assistant/issues/653) | P1 Agent/Skill/identity/architecture | #433 #425 #385 #156 #274 #278 |
| [#654](https://github.com/boxs-51/assistant/issues/654) | P2 Future WEB-DL/CAS/game | #165 #166 #221 |
| [#655](https://github.com/boxs-51/assistant/issues/655) | P0 Full exact-alpha CI / divergent file audit | #590 #586 |
| [#656](https://github.com/boxs-51/assistant/issues/656) | P1 Docs & Owner procedural decision | #560 |

All **34 original issue references** are archived only after successor custody exists. Closing an issue means “superseded by new open technical tracker”, **not** “completed” or “safe to deploy”. Continue using source PR and issue links for real acceptance evidence. Archive old open PRs with explicit no-merge/no-source-loss note; never delete branches or force-push.

## 7. Exit limits and recommended sequence (no repairs executed)

1. Re-run complete **exact alpha HEAD** CI and read raw per-OS JUnit/Redis/PG17, classify every failure and skip. CI result may be RED without contradicting archival goal.
2. Review 36 divergent blobs *per hunk*, preserving security boundaries and current-alpha canonical architecture; do not auto-accept old implementation.
3. Trace AIC/CAS/UBQ/CTX owner/migration and AE359/SBX3 critical E2E first; review docs and legacy-modern adapters next.
4. Issue scoped design/implementation authorization and RED-first baseline separately. A future promotion needs new Owner decision and independently verified platform checks.

**No production merge, deployment, test weakening, unreviewed permission expansion, or deletion of old evidence is authorized by this document.**
