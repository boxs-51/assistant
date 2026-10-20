# S02 — Alembic 31b/31t reconciliation diagnostic

Issue: #659; tracker #657; S01 #658; reference PR #666.
Branch: dev/alpha-s02-alembic-31b-31t-20261019
Frozen alpha source HEAD: 03905175640b0e893f0d30f65019a2eb17c14d89
State: DIAGNOSTIC / RED-FIRST ONLY; neither code nor release PASS.

## Admission decision
S01 #658 CLOSED completed for evidence/custody scope only (decision issue comment 6270876714). Draft #666 at 6d2ffd1b3ecf022a83e1462ff918d1201084cb34 was documentation-only: 9 final workflows (8 success / 1 failure). Linux full: 160 failed, 4278 passed, 91 skipped, 10 errors; 80 appearances were migration multiple-head failures, not 80 independent root causes. 71 owner/CAS/UBQ/CTX semantic failures, 17 PG17 bare-suite harness errors, and Native/issuer security holds remain in their own issues. This admits S02 diagnosis, NOT product/physical migration PASS.

## Observed Alembic graph at frozen alpha
    30a_ctx_f5_user_wide_memory_scope
       |                               |
       v                               v
    31a_aic0_agent_instance_identity   31t_direct_terminal_permission
       |
       v
    31b_aic0_pg_guard_forward

- 31a.down_revision=30a; 31b.down_revision=31a; 31t.down_revision=30a. There are TWO terminal heads before a fix.
- 31t only inserts terminal:execute if absent; NEVER inserts role_permissions. Its downgrade only deletes its own provisioned, unreferenced permission.
- 31b upgrades installed-31a PostgreSQL guard functions with trusted trigger checks, transaction lock and durable schema target; SQLite upgrade is no-op. PostgreSQL downgrade deliberately denies unsafe guard reversal.
- 5b_conversation_temporal_contract already provides a historical explicit Alembic merge example, independent of the present two tips.

## Frozen requirements
S02-01: Keep S01 raw Linux job 118105198348 as witnessed baseline RED and independently rerun new graph-targeted regression before any fix.
S02-02: Preserve 30a/31a/31b/31t revision IDs and old sources; no stamp, squash, down_revision reparent, schema overwrite, role grant, database production write, or test-security-oracle weakening.
S02-03: Preferred **design proposal**, not yet implemented: one NEW merge-only 32a_alpha_31b_31t_merge whose parents are (31b_aic0_pg_guard_forward, 31t_direct_terminal_permission), with no DDL/DML.
S02-04: Add isolated RED-first graph test checking exactly one effective head, exact merge ancestry and intact original tips.
S02-05: Future physical proofs: fresh and populated historical SQLite + PostgreSQL 17, upgrades starting from each tip, precise version-table identities, FK OFF/ON x recursive OFF/ON, AIC/CTX/UBQ negative owner oracles, preserved terminal permission grants, hard interruption/restart, transaction rollback/fail-closed, Linux and Windows exact candidate SHA.
S02-06: Keep distinct failures under #660 (semantic owner), #655 (PG17 CI harness), #662 (Native), #664 (metadata/issuer); a narrow S02 PASS is never platform PASS.

## Conflict ledger — DO NOT silently alter tests
C-S02-01 HARD_CONFLICT / UNRESOLVED:
- se/tests/integration/test_terminal_permission_provisioning_p1.py lines 110–119 explicitly expects TWO heads {31t,31b}; following tests check terminal permissions and exact role grant authorization.
- se/tests/architecture/test_ubq2_dual_accounting.py lines 47–101 demands one head but only permits existing 5b as a two-parent merge, so a newly appended explicit merge will fail its historical graph-shape oracle.
- S02 demands ONE head and original security/negative assertions unchanged. Do not rewrite old migration parents to satisfy tests or quietly alter these oracles. First demonstrate any future narrow topology-test adaptation retains its original security/lineage guard strength and is separately reviewable. Until resolved, full CI is not expected GREEN.

C-S02-02 ROLLBACK_GUARD:
- PostgreSQL 31b refuses a guard-removing downgrade. Do not bypass it. A valid interruption/rollback proof should validate preserved state and fail-closed behavior; do not claim unrestricted PG downgrade.

## Implementation checkpoint (code present, NOT VERIFIED)

Draft PR #667 now contains:
- NEW merge-only 32a_alpha_31b_31t_merge.py with strict parents (31b,31t), no role grant, DML, DDL, stamp or reparent.
- ADD-only RED-first graph test, ADD SQLite historical regression covering 30a/31b/31t x SQLite FK/recursive OFF/ON with real temporary databases and owner/permission denials.
- ADD strict explicit-only PostgreSQL17 physical test at se/physical/s02_pg17_physical.py, three disposable loopback-only databases (30a,31b,31t) and immutable pg catalog/role/owner checks.
- ADD dedicated Linux/Windows SQLite and Linux PostgreSQL17 CI workflow at .github/workflows/alpha-s02-alembic-physical.yml; raw JUnit artifact paths defined.
- Topology-only exact changes to 14 historical DAG shape tests plus terminal provisioning test: older required merge 5b and fork 4f remain pinned, new merge 32a and fork 30a added explicitly; all role grant, authorization, data-owner and denial assertions unchanged.

**Validation status:** all new/old exact-head GitHub workflows were queued on last inspection. No observed post-fix SQLite/PG17 runtime PASS, no exact-HEAD full-suite GREEN, and no completed PR #667 baseline RED-first CI. A local reconstructed Alembic metadata fixture observed two heads before adding 32a and one afterward; it is NOT a substitute for real repository/physical test proof. S02 = IN_PROGRESS / UNVERIFIED; S03 HOLD. Real PG17 rollback of 31b remains fail-closed, not a successful security downgrade. Full existing platform SEMANTIC_RED remains separately held under #660/#655/#662/#664.

## Gate
No alpha/main merge, deployment or production database updates. Do not promote S02 until exact-head regression, actual raw JUnit, targeted physical SQLite/PG17 and separate reviewer provenance are verified.
