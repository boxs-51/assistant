# S01 exact-alpha integration baseline witness (evidence-first)

**Stage issue:** [#658](https://github.com/boxs-51/assistant/issues/658) · **tracker:** [#657](https://github.com/boxs-51/assistant/issues/657) · **parent CI custody:** [#655](https://github.com/boxs-51/assistant/issues/655).  
**Issue contract:** S01 of [linear sequence](../LINEAR_DELIVERY_SEQUENCE.md).  
**Source baseline:** alpha@03905175640b0e893f0d30f65019a2eb17c14d89 (docs-normalized), branch dev/alpha-s01-evidence-baseline-20261019.  
**Execution status at authorship:** NOT_RUN for S01 Draft PR, NO_SOURCE_FIX, NO_ORACLE_CHANGE, NO_MERGE, NO_DEPLOY, no production approvals.

## Aim
Trigger ordinary `pull_request` GitHub Actions from a minimal docs-only Draft PR, so that current alpha code, tests, and CI can be observed on one exact candidate HEAD without an unreviewed source patch. A baseline with failures is useful evidence, not grounds to weaken the expected negative assertions.

## Required evidence matrix
| Domain | Required observation | Status at authorship |
|---|---|---|
| Architecture Linux | raw pytest/JUnit for full `python -m pytest -q`, classify every failing selector | NOT_RUN |
| Windows CL | `python -m pytest -q cl/tests`, runner info and raw stdout/JUnit if emitted | NOT_RUN |
| Windows SE/Tools | architecture portable Windows matrix, not substitute for OS isolation | NOT_RUN |
| PostgreSQL 17 | physical Linux and Windows correct real DB version, populated historical migrations, exact SHA | NOT_RUN for this PR |
| Redis + PG17 | R-ACK cross-instance strict physical, ACK-loss, one-valid-OTP, fail-closed | NOT_RUN for this PR |
| SQLite FK/trigger matrix | actual schema, REKEY/REPLACE/owner integrity, `FK OFF/ON × recursive OFF/ON` | NOT_RUN |
| Native D2/C2 | portable negative vs real OS kernel child/descendant evidence independently labelled | NOT_RUN |
| Old 9 PRs divergence | 147 changed path appearances; 111 SHA matches / 36 different vs old pre-consolidation alpha | PENDING semantic-hunk investigation |

Only attach actual run IDs, JUnit artifacts, commit SHA, per-selector output, run environment and semantic classification after completion. Missing service/environment or intentionally RED security negative must never become a false PASS.

## Confirmed pre-existing evidence (not S01 pass)
- alpha@0390517 source tree inherits two Alembic heads: 31t and 31b, separate descendants of 30a; no merge revision observed at prior source HEAD. Do not use `alembic stamp` as a workaround.
- Older alpha@76842cf AIC467 PG17 [run 39258636299](https://github.com/boxs-51/assistant/actions/runs/39258636299) success is scoped, not full alpha.
- PR #648 [Architecture Baseline 39157350888](https://github.com/boxs-51/assistant/actions/runs/39157350888) was on an earlier distinct HEAD and failed Linux; its data must not be relabelled as the present S01 result.
- PR #647 [R-ACK strict run 39150765774](https://github.com/boxs-51/assistant/actions/runs/39150765774) and PR #648 Native D2 runs were branch-scoped narrow positives/negatives.

## Exit and S02 admission
S01 is ready for evidence classification only when actual exact-head job results can be inspected. To call S01 PASS: all 36 divergent appearances receive a hunk-level custody decision with source refs, every mandatory OS/DB/security check has an authentic result or explicit HOLD, and the next S02 test contract is frozen. A failed full-suite may still support a bugfix RED baseline, but it does NOT certify architecture health or production readiness. S02 development branch remains unopened until sufficient S01 evidence is available.

## Security invariant
Keep original negative assertion identities, no skips/xfails, no changing security authorization or issuing Native effects; no opaque environmental fallbacks to SQLite for a PG17 strict run. This file is **documentation only** and does not grant any runtime capability.
