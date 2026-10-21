<!-- POLICY85_HISTORICAL_GOVERNANCE_20261016:START -->
> **RETIRED / NON-NORMATIVE historical governance.** Policy #85 is no longer in effect. Technical P0/P1/HOLD findings, existing security guards and test expectations remain independently binding where applicable; this document cannot reactivate retired procedural instructions or authorize merge/deployment.
<!-- POLICY85_HISTORICAL_GOVERNANCE_20261016:END -->

# PDRM v1.0: document provenance and reconciliation for alpha

**Assessment baseline:** alpha@76842cf56f40faab3083c6de390373ef56fb65f2, 2026-10-19. Inventory contains 233 docs paths; representative high-risk contracts were read deeply. **NOT** a 233-file line-by-line certification. No retroactive change to source approvals or release authority.

## Working classification
| Document | Classification | Effective meaning |
|---|---|---|
| README.md and docs/alpha/* | CURRENT_STATE / DEVELOPMENT_PLAN | New navigation and pending stage gates, not production runtime evidence |
| docs/alpha-integration-ledger-20261018.md | HISTORICAL_REFERENCE | Snapshot of old open PRs, not live issue count |
| docs/alpha-consolidation-and-review-20261019.md | HISTORICAL_REFERENCE | Pre-doc-commit merge audit and residual differences |
| docs/ROADMAP_NAMESPACE_REGISTRY.md | MIXED_UNCLASSIFIED | Scope and namespace names useful; historical active-issue links need status checks |
| docs/legacy/CL_SE_Architecture_Contract_v1.md | HISTORICAL_REFERENCE with compatibility invariants | DIRECT/AGENT responsibility; no implicit rewrite |
| docs/capability_runtime/AGENT_ONLY_CAPABILITY_SANDBOX_CAS_CONTRACT_REFREEZE.md | PLANNED_PROPOSED / mixed historic audit | Target Agent-only design; source still has DIRECT compatibility |
| docs/capability_runtime/SBX3_S1A_PRIVILEGED_IPC_CONTRACT.md | PLANNED_PROPOSED | Deny-only transport; no privileged spawn/certification |
| docs/agent_interconnect/*, docs/agent_platform/* | MIXED_UNCLASSIFIED | Identity contracts and future profiles; stage-by-stage runtime proof needed |
| docs/central_asset/*, docs/context_future/*, docs/user_budget_quota/* | MIXED_UNCLASSIFIED | Technical owner limits endure; release states require exact tests |
| docs/skill_v2/*, docs/agent_automation/* | PLANNED_PROPOSED + partial CODE_PRESENT | Manifest/trigger names never grant capability rights |
| AGENT.md | HISTORICAL_REFERENCE | Retired repository governance only; independent technical HOLD intact |

## PDRM control ledger
| Controls | Result |
|---|---|
| PDRM-01..02 inventory/revision | all paths enumerated; representative file reads + pinned original SHA; independent approval provenance unverified |
| PDRM-03..04 authority/time | historical snapshots distinguished from active technical requirements |
| PDRM-05 stages | source existence != verified or deployed, stage labels need feature-specific evidence |
| PDRM-06 code→docs | README navigation gap addressed by new current index; follow-up operational quickstart E2E unverified |
| PDRM-07 docs→code | Agent-only is target, DIRECT remains intentionally wired; no automatic violation |
| PDRM-08 MARM | legacy-modern intentional hybrid preserved |
| PDRM-09 migrations | Historical dual-head 31t+31b fixed in nonproduction alpha by merge-only 32a PR #667; forward-only owner integrity 32b PR #668 follows; production installed-data upgrade requires fresh preflight |
| PDRM-10 interfaces | actual DTO/serialization/auth/driver E2E incomplete |
| PDRM-11 security | no issuer/grant inferred from Skill/docs; production Native hold |
| PDRM-12 verification | PR #668 S03/Architecture matrix PASS at PR HEAD; exact merge-SHA AIC467 PG17 58/Linux + 58/Windows PASS, not a full post-merge E2E certificate |
| PDRM-13 cross-document | old ledgers are correctly historical, NOT a current conflict |
| PDRM-14 traceability | #649–#656 parent custody; S01 #658 evidence-only closed, S02 #659 and S03 #660 scoped PASS/closed, #661 onwards unresolved |
| PDRM-15 authority | doc-only normalization authorized; source/test edits NOT authorized at S00 |
| PDRM-16 closure | task, documentation, software readiness statuses reported separately |

## Verified post-S03 alpha checkpoint (2026-10-21; historical assessment retained)
- At `alpha@300801d67c43d702408d2895f7c89898e27a87fd`, S02 #659 via PR #667 (32a) and S03 #660 via PR #668 (32b) were completed for scoped **nonproduction** proofs.
- PR-head S03 physical matrix: SQLite Linux 85 PASS, Windows 85 PASS, strict PG17 9 PASS, portable/dual-lane CI PASS. Merge-head AIC467 PG17 Linux and Windows each 58 PASS; full merge-head Architecture/S03 E2E unverified (#655/#665).
- Automatic Codex **Code Review** on PR #668 completed at exact PR HEAD with no findings; **Security Review not run**. Alpha does not require branch protection per Owner; release/main obligations are separate.
- Remote branch ancestry/PR custody: [BRANCH_CLEANUP_AUDIT_20261021.md](BRANCH_CLEANUP_AUDIT_20261021.md). A deletion candidate is not a deletion receipt.

## Current doc→code reconciliation
- Source in se/src/main.py bootstraps DirectChatRuntime and AgentRuntime; se/src/domain/schemas/request.py chooses DIRECT by default. Treat Agent-only roadmap as PLANNED, preserve compatibility.
- Old docs referencing former Policy #85 as current must be interpreted with the repository's retirement banner; no revival or tool access grant.
- Before S02, Alembic 31t and 31b were distinct heads. In `alpha@300801d6`, merge-only 32a joins them; forward-only 32b follows. Operational docs MUST NOT recommend a generic `upgrade head` on live data without environment-specific installed-state, grants, backup, rollback and failure-mode preflight.
- Native negative receipt guards are CODE_PRESENT and not genuine OS-issued permission. Full OS proof UNVERIFIED.
- Current issue inventory has superseded historic issues; closed historical issue ≠ technical PASS.
- No asserted changes to old specification facts merely to reconcile current code.

## Suggested future documentation maintenance (not authorized code changes)
Add exact source revision, technical owner, review date, status of activation, ABI/interface version, test evidence link and replacement pointer whenever a stage is actually verified. Preserve historical URLs and old RED material. Keep only one entrypoint describing present development state; stage proposals retain their own scope.

## Scope and restrictions
This is a planning and traceability overlay. The human Owner's procedural review exception preserved from historical #560 does not waive OS physical/security tests or GitHub gates. No deployment or main merge is authorized. Changes beyond docs require fresh bounded source request/approval.
