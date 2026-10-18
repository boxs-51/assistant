# Alpha integration conflict resolution workplan — 18 October 2026

Verified current alpha ancestor: `6ccabaadab226cfe440de0842e352be60b83657d`. This plan is authored on `dev/alpha-consolidated-next-20261018`, not on `main`.

**Purpose:** Continue safe development after 28 alpha-only integration PRs; preserve source and target variants of 13 still-DRAFT alpha-stage PRs. The Owner accepts RED tests on nonproduction alpha but **does not authorize silent overwrite of contradictory source files**, production promotion, Native privilege activation or closure of original issues.

At this pinned tree: **85 matching path blobs**, **40 divergent path blobs**, **0 missing paths** across the 13 stage PR file lists. A file's presence is NOT proof of semantic merge, and a same hash can belong to several stacked sources.

| Alpha-stage PR | Original PR | Exact blob matches | Divergent existing paths | Missing paths |
|---|---|---:|---:|---:|
| [#609](https://github.com/boxs-51/assistant/pull/609) | #583 | 16 | 6 | 0 |
| [#612](https://github.com/boxs-51/assistant/pull/612) | #579 | 33 | 4 | 0 |
| [#613](https://github.com/boxs-51/assistant/pull/613) | #555 | 4 | 2 | 0 |
| [#614](https://github.com/boxs-51/assistant/pull/614) | #549 | 19 | 6 | 0 |
| [#623](https://github.com/boxs-51/assistant/pull/623) | #487 | 3 | 2 | 0 |
| [#627](https://github.com/boxs-51/assistant/pull/627) | #465 | 3 | 7 | 0 |
| [#629](https://github.com/boxs-51/assistant/pull/629) | #503 | 2 | 1 | 0 |
| [#630](https://github.com/boxs-51/assistant/pull/630) | #505 | 2 | 1 | 0 |
| [#632](https://github.com/boxs-51/assistant/pull/632) | #494 | 1 | 2 | 0 |
| [#633](https://github.com/boxs-51/assistant/pull/633) | #493 | 0 | 2 | 0 |
| [#634](https://github.com/boxs-51/assistant/pull/634) | #491 | 0 | 1 | 0 |
| [#636](https://github.com/boxs-51/assistant/pull/636) | #547 | 1 | 2 | 0 |
| [#637](https://github.com/boxs-51/assistant/pull/637) | #537 | 1 | 4 | 0 |

## Divergent path custody — exact source vs alpha blob identities

### #609 (original #583)

- `.github/workflows/sbx3-agent-process-deny-two-os.yml` — source `a0ffe0dac2b837d440db3c6ab11bd8f0fa0dc409`; alpha `c4682d9fe0f28f7d42d69682c4f3829744da20a3`
- `.github/workflows/sbx3-model-origin-ingress-two-os.yml` — source `f83394b535854bb9f369bd584cf004fef6709953`; alpha `9e3801a61757ebeee57ec07a0b33c4876145811a`
- `cl/src/core/capability_runtime.py` — source `46a78b820deab354bcd6e72530846bc05bab0aee`; alpha `6c1e0fb56e3f3b3ce43128d0f9e84dd86ef5d3c0`
- `cl/tests/test_ta_r3_p0_mcp_alias.py` — source `ce1688b4c73db33e95dff7524d0d176cc5ba75bc`; alpha `a96161d62ab9410ed3c2158dc89d71de6df0afad`
- `se/src/runtimes/capability/runtime.py` — source `0a4026fde2ca91af08e1da01f9d5ea108be5ff0f`; alpha `bb33b9d649fd6f4ac784ed072d051b04eee28892`
- `se/tests/e2e/test_sbx3_agent_client_ingress_real_tcp_red.py` — source `2dcc25cc674a7a8bee2b1502898606198d01e06a`; alpha `76bf7c703838b579149749e3495427dc044fc78d`

### #612 (original #579)

- `.github/workflows/ae359-otp-db-atomic-pg17.yml` — source `0b200fb92da84d95f3507b76892b118a1db2cfb0`; alpha `2b6c87b6e766bd569b98721bb13e2e033595d6ca`
- `.github/workflows/ae359-post-ack-loss-physical.yml` — source `cc747492fa9ef2ce6a90937b2ff5cd444b2ec305`; alpha `ade116ea3d9f4717b36d27d0f72bae45fb7c7bdc`
- `.github/workflows/ae359-utc-naive-pg17-redfirst.yml` — source `a65bb7578910e19b0f254a3d43f3abb908d50563`; alpha `0e3d24ba6484b8f8fb33685e1d6c461b08b83953`
- `.github/workflows/architecture-baseline.yml` — source `59c7e237ece6e9c9f058935d464b7e452650361a`; alpha `342efc1053d6ea2390e6da659a28291c55d80336`

### #613 (original #555)

- `.github/workflows/architecture-baseline.yml` — source `32feeaa4dd0d568819ab8b2b69fcc5aff2236a8a`; alpha `342efc1053d6ea2390e6da659a28291c55d80336`
- `se/tests/e2e/test_sbx3_direct_capability_real_jwt_auth.py` — source `c648dd2199afe1252960e457a836b476554dc134`; alpha `8f6063ab114fb6687d0ddd446edc5704abe4c4c5`

### #614 (original #549)

- `.github/workflows/ae359-logout-cache-redfirst.yml` — source `3f9e8967994f8185fc165d75c6993d6c1b08820e`; alpha `9e078b74a3579dc417674a11832760d9634b188b`
- `.github/workflows/ae359-otp-db-atomic-pg17.yml` — source `d051fe65c5782088e7d811ec4d40454553236c4d`; alpha `2b6c87b6e766bd569b98721bb13e2e033595d6ca`
- `se/src/infrastructure/storage/drivers/redis/driver.py` — source `0e46caa1e912935ff9c68d8bec6c0f7ef33985b5`; alpha `ac8ba60a526477ec224ac9fcf62b4318ab1cc16d`
- `se/src/infrastructure/storage/interfaces/cache.py` — source `f3a859e4d8655c06fe70ab09b99f7afd510b5f72`; alpha `a8adc55f763dee6f6e2a09b2cfb4fffb459e316f`
- `se/src/infrastructure/storage/repositories/user_data/pending_registrations.py` — source `d03b90244a7123d29bc2057ba6609362b3298ec3`; alpha `a6d0a0f075e10306ec1c69626550cce45daf3409`
- `se/src/transport/gateway/authentication/services/otp_service.py` — source `7350dd917ccd60ac9be6da8fc1d8a86bb09e38b7`; alpha `7a50463fc753d6e5bb31e7167500dabccaf1b1a5`

### #623 (original #487)

- `se/src/runtimes/capability/native_invocation_authority_b0.py` — source `7b477631a9a52a983c002873eb52344d00bd5618`; alpha `3ce4b7e45c4efddd3425e97d3d97a990b047c452`
- `se/tests/architecture/test_sbx3_invocation_authority_b0.py` — source `414997a52671dd3556b6f897927af2831486c56f`; alpha `2b22a923209d3417ccc28a86de4c5140fac01443`

### #627 (original #465)

- `se/src/runtimes/agent/persistence.py` — source `0c79447c3c937d7c61e5d071cc52c3c9d50d33e5`; alpha `63af3862010822231c8e162da7737b83c826450a`
- `se/src/runtimes/agent/recovery_execution.py` — source `9ce8aa169b18f8244cc61a997e6532124cce41e2`; alpha `9c7906d30a92f1b7d846bd81d9adb996ee2b5e1d`
- `se/src/runtimes/agent/resume_planning.py` — source `2741b71fa58f1e6714b688d3b20777c23cfe1ebb`; alpha `3539a68b2932503eaf3c05269b6bad29789be2ca`
- `se/src/runtimes/agent/runtime.py` — source `815a7e1cce9e1201cc137a2e07514097003ae886`; alpha `399533ac131918aa6f7bb18c55f877a4b3e0d243`
- `se/src/runtimes/capability/runtime.py` — source `83dd9314011aa5b8379e1393c557c0afa2f71f37`; alpha `bb33b9d649fd6f4ac784ed072d051b04eee28892`
- `se/src/runtimes/capability/sandbox.py` — source `2c3e0519580043ae8627b921877d1443e9f0c441`; alpha `d59097846589bd8f408dab6dae441a43d0d177c3`
- `se/tests/architecture/test_dcs1_capability_selection.py` — source `0d4bc77944d5e3f77ab45dbc176e738d3ce3670d`; alpha `e20b82a91ccbb03af1306259584aa7884af414ce`

### #629 (original #503)

- `.github/workflows/aic31b-pr494-pr496-pr500-composed-pg17.yml` — source `ccf1d198d3e615fec544f6c965f03f871979cde3`; alpha `4c3cb0f811ebd5b8afdca04751242b4ebea19df2`

### #630 (original #505)

- `.github/workflows/aic31b-pr494-pr496-pr500-composed-pg17.yml` — source `62de9f96f75f623ebd3b7aa492135e088baa8252`; alpha `4c3cb0f811ebd5b8afdca04751242b4ebea19df2`

### #632 (original #494)

- `.github/workflows/aic467-pg17-linux-windows-evidence.yml` — source `a290365cb081d562e947ffefbe22ad99d2c561ef`; alpha `8643d1a4e74dd5ad7dd8432b61609b02886c1650`
- `se/tests/integration/test_aic0_p1_agent_instance_identity.py` — source `76695d42c95be675dcb2fa410eb29ef5d17ac413`; alpha `55c993ba49548b9cece8206eed5ef4ea008a0e11`

### #633 (original #493)

- `.github/workflows/aic467-pg17-linux-windows-evidence.yml` — source `ef063317b5f30f96666814c4e93944429e787f02`; alpha `8643d1a4e74dd5ad7dd8432b61609b02886c1650`
- `se/src/infrastructure/storage/migrations/sql/versions/5b_conversation_temporal_contract.py` — source `698ca49faca2e46c456ee962c666756e19edef7f`; alpha `20be86d8e3852027e411a0b16e0a8015993b4998`

### #634 (original #491)

- `.github/workflows/aic467-pg17-linux-windows-evidence.yml` — source `9de14f1b15bd0a373adfe40a03e84461a8ea9a55`; alpha `8643d1a4e74dd5ad7dd8432b61609b02886c1650`

### #636 (original #547)

- `.github/workflows/architecture-baseline.yml` — source `32feeaa4dd0d568819ab8b2b69fcc5aff2236a8a`; alpha `342efc1053d6ea2390e6da659a28291c55d80336`
- `se/tests/e2e/test_sbx3_direct_capability_real_jwt_auth.py` — source `c648dd2199afe1252960e457a836b476554dc134`; alpha `8f6063ab114fb6687d0ddd446edc5704abe4c4c5`

### #637 (original #537)

- `.github/workflows/ae359-logout-cache-redfirst.yml` — source `9eebde9b29fdd9afaa740299055f99d605e5e0d5`; alpha `9e078b74a3579dc417674a11832760d9634b188b`
- `se/src/infrastructure/storage/repositories/sessions.py` — source `b99e2b16fa2a070a9b9ff6b810afead2c0a02f75`; alpha `f5b116694d8efe637ea949d379142f199b947c23`
- `se/src/transport/gateway/api/v1/auth_router.py` — source `514b8e8c63f884071c8aba0bac4c3899503b132a`; alpha `370c6696659565bc18e6dd599ea1add6c58687cb`
- `se/tests/integration/test_ae359_logout_cache_redfirst.py` — source `bc2b3cafdfdf4fe1728fbe0be1e2ad5191603b14`; alpha `3af8407a55646315be504e0d173eb19399a70032`

## Nonproduction development sequence

1. **AIC historical PG17/Alembic oracles:** #629, #630, #632, #633, #634. Preserve current forward migration semantics and version width, compare each differing workflow or migration file against current alpha, never run real database migration automatically.
2. **AE359 logout/OTP/refresh concurrency:** #612, #614, #637. Preserve ACK-loss / R-ACK-07 RED, Redis/PostgreSQL uncertain COMMIT, same-challenge one-use and no false logout 204 ACK. Do not assert cross-store durability from portable tests.
3. **SBX3 Model-origin/MCP, Native custody and Direct auth:** #609, #613, #623, #627, #636. New B0 modules/evidence copied non-destructively through #638, but their overlapping activation/runtime/security changes are still unresolved. Keep Native Agent DENY and P0 C2/signed-origin gates on HOLD.
4. For each path, inspect three-way common ancestor + target + source; add RED-first repro for lost security invariant; create bounded child branch from live alpha, merge reviewed file/line-level deltas, run Linux/Windows/physical infrastructure tests where available, record known RED separately.
5. Do NOT use ours-only octopus merges that merely hide source differences; do not disable workflow jobs, weaken assertions, remove skips without proof, close bug issues or force-update legacy PR branches. Build/deploy/production migration remain forbidden.

**STATUS: ALPHA_ONLY; P0/P1 TECHNICAL AND SECURITY HOLD PRESERVED; NO_MAIN_MERGE; NO_DEPLOY; ISSUES_UNCHANGED.**


## Codex Engineering Workflow conflict triage C1 — 2026-10-18

**Goal (REQ-C0):** continue alpha-only convergence while preserving all source issues, original PRs, security oracles and HOLD, NO_DEPLOY/NO_MAIN_MERGE. Exact alpha before work: `6ccabaadab226cfe440de0842e352be60b83657d`.

### Decision ledger

| Decision | Alpha-stage sources | Evidence/decision | State |
|---|---|---|---|
| CR-AIC-WF-01 | #629/#630 (original #503/#505) | Workflow `aic31b-pr494-pr496-pr500-composed-pg17.yml` compares **exactly 4 removed + 4 added lines**, consisting only of workflow name, old `push.branches` selector and two comments. The remaining 138 lines, including strict PostgreSQL 17 Linux/Windows test commands, JUnit 2/0/0/0 and actual PostgreSQL service setup, are exact line matches. Alpha retains newer PR504 NOWAIT workflow body. | `PROCEED_SAFE`: **executable content reconciled**, historical branch-specific push triggers intentionally not activated on alpha. Source PRs remain open. |
| CR-AIC-WF-02 | #632/#634 (original #494/#491) | Workflow `aic467-pg17-linux-windows-evidence.yml` differs only in name + historical `push.branches` selector for #632 (2+2 lines) and selector alone for #634 (1+1). Test command and strict 0-skips checks match exactly. | `PROCEED_SAFE`: no old-target workflow overwrite necessary; historic push-selector intent remains source metadata, not alpha's new permission. |
| CR-SBX3-SUPERSET-01 | #613/#636 (original #555/#547) | Of 6 paths in #613: 4 exact blob matches; the workflow alpha file is an exact source **prefix** with one additional target line; source JWT real E2E test file is an exact line **subsequence** of alpha (529 source vs 813 alpha lines), no original `test_*` names missing, plus alpha adds J11/J12/J13/J14 security negatives. For #636: 1 exact blob plus the same 2 supersets. | `PROCEED_SAFE`: historical test/workflow file conflicts are functional **strict-supersets**, not missing functionality. Do not revert alpha, self-approve native positives, close original PRs, or claim independent review. |
| CR-AIC5B-02 | #633 (original #493) | Historical `5b` migration has ten source-only lines, PostgreSQL-only `alembic_version.version_num` widening; source blob `698ca49faca2e46c456ee962c666756e19edef7f`. Created [DRAFT #639](https://github.com/boxs-51/assistant/pull/639) against alpha, exact source blob plus two-OS negative/positive workflow. Workflow pins original `env.py` AND `5b` at immutable `main@858004fae17732f3b388383c28146c12b36ecdb9`. | `IN_PROGRESS`, not merged; PostgreSQL17 Linux baseline **observed genuine StringDataRightTruncationError** before fix, but first PR #639 run's subsequent positive failed because shallow Git checkout lacked historical 31a commit. New exact-head workflow explicitly fetches full history, no oracle skipped. Await fresh Linux/Windows run. |
| CR-AIC-PG17-03 | #632 (original #494) | Exactly four original PG17 test functions absent in alpha: 3-scenario version-preflight, 3-scenario unsafe-search-path, read-only migrator and hard-kill-after-preflight. Appended their source tail unchanged on stacked [DRAFT #640](https://github.com/boxs-51/assistant/pull/640); unchanged existing target tests and assertions. | `IN_PROGRESS`: physical PG17 Linux/Windows CI and an independent security review still needed. #640 has fast-forward-preserving merge commit from latest parent #639; original #632 remains untouched. |
| CR-HOLD-04 | #609/#612/#614/#623/#627/#637 and residual #632/#633 | Divergent runtime/authorization/cache, migration and kernel/native files are **not** automatically safe merely because their paths exist. Use exact ancestor diff, target negative oracles and physical proof; never prefer old full blob or ours-only blind merge. | `HOLD`, continue bounded RED-first child patches. |

**Status terms are deliberately separate:** `EXECUTABLE_EQUIVALENCE` / `STRICT_SUPERSET` are static scoped custody findings, **not actual GitHub MERGED events**, not proof of platform runtime correctness, and not grounds to close existing issue/PR records or remove security HOLD. Same/overlapping code remains in alpha; upstream historical trigger names were not copied because they are not relevant to alpha behavior.

**Scope summary:** 13 staging PRs remain OPEN/DRAFT in GitHub. #639/#640 are new DRAFT non-production code/test remediation PRs; neither has merged into alpha. No content/issue status changes to main, #613, #636, #629/#630/#632/#634, or original sources.


## Contract C2 — physical RED-first reproduction and bounded security fix

### CR-SBX3-REG-MCP-01 — original #609/#583

- **[DRAFT #641](https://github.com/boxs-51/assistant/pull/641)** test-only @ `0c4bb9b609316995720522e9c20d3c9ac97afd5c`: test blob **exactly matches original #609 source** `ce1688b4c73db33e95dff7524d0d176cc5ba75bc`. Two unchanged test functions parametrize 8 MCP marker forms plus 2 non-MCP V1/V2 shapes.
- Actual **Windows client** baseline [job 117686062553](https://github.com/boxs-51/assistant/actions/runs/39131034571/job/117686062553): `8 FAILED, 340 PASSED` (6 MCP None/False/string KeyError on missing explicit false, 2 non-MCP cases leaked `trusted_direct_read_only=True`). This is attributable `BASELINE_RED`, not unrelated infra. No skip or assertion modification.
- **[DRAFT #643](https://github.com/boxs-51/assistant/pull/643)** fixes a single client registration source file on top of #641. Resulting blob SHA **exact original #609 source** `46a78b820deab354bcd6e72530846bc05bab0aee`: local exact True for MCP only; explicit False for other MCP values, omit certification for all unrelated non-MCP V1/V2. No physical native authorization activated. **POSTFIX test checks are still pending**; review must distinguish this fix from unrelated RED tests. Original #609 remains open and diverges in other source paths.

### CR-AIC31B-MULTIHEAD-01 — alpha composition

- Actual physical PG17 composed baseline at #640 [Linux job 117684933490](https://github.com/boxs-51/assistant/actions/runs/39130648710/job/117684933490) fails both `actual_stamped_old31a` and `pristine_to_31b` because `alembic upgrade head` has multiple heads in the aggregated alpha DAG; old PR494 checkout `head` remains valid and is not touched.
- **[DRAFT #642](https://github.com/boxs-51/assistant/pull/642)** changes **only** the alpha worktree CLI target from ambiguous `head` to the test's already asserted immutable `REV31B="31b_aic0_pg_guard_forward"`. Strict retired identity/security oracles unchanged. Exact-head composed-linux subsequent check **PASS**. Composed-windows evidence / independent review pending at initial observation.
- Original AIC/PG17 physical tests from #640 on Linux and Windows: pinned 5b NEGATIVE_CONTROL_PASS followed by **58 PASS / 0 skipped on each OS** ([Linux job 117684933602](https://github.com/boxs-51/assistant/actions/runs/39130648708/job/117684933602), [Windows job 117684933304](https://github.com/boxs-51/assistant/actions/runs/39130648708/job/117684933304)). The other real PG17 pair also returned **56 passed, 2 deselected** plus a strict historical SQLite test. Global suite remains RED due unrelated security/integration branches.

### CR-AE359-RACK07 — unrelated preserved hard failure

- On #639 exact-head physical [job 117684371176](https://github.com/boxs-51/assistant/actions/runs/39130455104/job/117684371176), test `test_r_ack_07_lost_ack_then_fresh_uninitialized_cache_must_not_issue_sql_otp` reports **SEMANTIC_RED: fresh-instance pre-dispatch fallback issues a second SQL OTP following uncertain Redis ACK** (`1 failed, 8 passed`). This is genuine P0 / cross-instance durability gap, not a configuration or harmless test skip.
- Never weaken/xfail this oracle, claim release-readiness, or merge the broad alpha tree to main until separately remediated and physical proofs succeed. It does not authorize abandonment of independent safe AIC and SBX3 child work.

### CR-AE359-P0A-SUPERSET — original #637/#537

- `auth_router.py` and `test_ae359_logout_cache_redfirst.py` original source files are **exact line subsequences** of alpha; alpha test adds seven P0B/atomic-delete/concurrency/Redis receipt witnesses. Alpha `SessionRepository.delete_token` returns **verified int 0/1**, rejects null/boolean/no-op, and propagates Redis errors, strictly more protective than the historical #537 void delete and P0A-only exception propagation. Old full-file replacement would regress P0B.
- Historical #637 adds a PR path filter to the old cache RED workflow but does not warrant reverting safer current behavior. Classify **PARTIAL_STATIC_SUPERSET**, keep old PR open with original issue unchanged, no overwrite.

**Next gates:** after CI, run exact-head PR #643 Windows client tests; require post-fix `348 passed / 0 unexpected skipped` and preserve #641 RED provenance. Complete PR #642 Windows composed test. Keep #639/#640/#641/#642/#643 DRAFT pending independent review; alpha/main unchanged by C2 work.
