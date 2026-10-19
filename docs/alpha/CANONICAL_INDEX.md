# Alpha canonical documentation index — 2026-10-19

**Classification:** CURRENT_STATE index for development planning only. **Baseline source SHA:** 76842cf56f40faab3083c6de390373ef56fb65f2 (before documentation-only normalization). The current alpha commit may advance when docs are published; always re-read the actual branch SHA before claiming exact-head test evidence.

## Purpose / authority
This document is the navigational entrypoint for **alpha non-production**. It describes observations, not new rights or production certification. Human Owner requests, actual GitHub protections and independently retained technical P0/P1/security HOLDs remain separate from this index. No merge into main, deployment, waiver, privileged Native enablement, branch deletion or weakening of tests is authorized.

## Canonical development documents
1. [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md) — observed code and design boundaries, MARM legacy/modern and trust boundary.
2. [DEFECT_LEDGER.md](DEFECT_LEDGER.md) — issue map, evidence, risk and what has not been proved.
3. [LINEAR_DELIVERY_SEQUENCE.md](LINEAR_DELIVERY_SEQUENCE.md) — strict stage order and exit gates.
4. [DOCUMENT_STATUS_MATRIX.md](DOCUMENT_STATUS_MATRIX.md) — historical/proposed/current classification and reconciliation.
5. [README.md](../../README.md) — repository entrypoint and test limitations.

These documents are current **indexes/assessments** only; they do not rewrite immutable historical requirements. A newer user-authorized technical contract must be evaluated within its exact scope before it can supersede an older one. Old snapshots are not today's release status.

## Verified GitHub snapshot (2026-10-19)
- Initial alpha at assessment: 76842cf56f40faab3083c6de390373ef56fb65f2; main: 858004fae17732f3b388383c28146c12b36ecdb9.
- At inventory, open PR count 0; open issue count 8 (#649–#656).
- Git tree: 1,539 blobs, 216 directories, 233 docs paths and 33 .github/workflows YAML files (recursive result not truncated).
- AIC467 PG17 GitHub run [39258636299](https://github.com/boxs-51/assistant/actions/runs/39258636299) succeeded on alpha@76842cf with Linux and Windows jobs. It is **not a full-suite pass**.
- Before integration, PR #648 Architecture Baseline run [39157350888](https://github.com/boxs-51/assistant/actions/runs/39157350888) had a failed Linux full-suite. It is not an exact-current-alpha result.
- PR #647 R-ACK physical and #648 D2 negative evidence were **branch-scoped**, not a final end-to-end production certificate.
- The old 9-PR inventory had 147 changed-path appearances, 111 blob matches, 36 divergent appearances, 0 new paths against pre-consolidation alpha; 36 is **not** 36 distinct independent defects. Hunk-level recovery is open.

## Evidence states used everywhere
- PLANNED: proposed design only.
- CODE_PRESENT: file/route exists, not proof the path is active.
- TESTED_SCOPED: stated selector and exact revision passed.
- VERIFIED_E2E: actual relevant boundary with authentic independent observations passed.
- RELEASED / DEPLOYED: separate real release/host evidence (none inferred here).
- BLOCKED / SECURITY_HOLD / UNVERIFIED: never translated to PASS by source existence.

## Issues and custody
Historical issues and PR discussions remain readable; administrative closure did **not** resolve their technical defects. Current carrying trackers:
[#649](https://github.com/boxs-51/assistant/issues/649) data/migration; [#650](https://github.com/boxs-51/assistant/issues/650) OTP/refresh; [#651](https://github.com/boxs-51/assistant/issues/651) OS Native; [#652](https://github.com/boxs-51/assistant/issues/652) client/HITL; [#653](https://github.com/boxs-51/assistant/issues/653) AIC/APR/SKV2; [#654](https://github.com/boxs-51/assistant/issues/654) future features; [#655](https://github.com/boxs-51/assistant/issues/655) CI/36 differences; [#656](https://github.com/boxs-51/assistant/issues/656) docs/Owner decision. Specific execution issues will link their parent and acceptance proof.

## Historic audit sources
- [2026-10-18 integration ledger](../alpha-integration-ledger-20261018.md): HISTORICAL_REFERENCE.
- [2026-10-19 consolidation](../alpha-consolidation-and-review-20261019.md): HISTORICAL_REFERENCE for the pre-doc commit, with technical risks retained.
- [Roadmap namespace registry](../ROADMAP_NAMESPACE_REGISTRY.md): mixed namespace contract + historical progress.
- All docs/legacy, docs/agent_execution_r*, docs/context_future, docs/capability_runtime, docs/skill_v2 etc are stage-specific contracts/proposals; **do not treat them all as current runtime behavior**.

## Change discipline
Documentation-only updates may improve navigation/status without source changes. Preserve lineage, URLs, effective phase and original P0/STOP oracle. New plans are NOT_OWNER_APPROVAL; any future repair must start with the issue's frozen acceptance and observed RED/negative baseline. No main merge or deploy.
