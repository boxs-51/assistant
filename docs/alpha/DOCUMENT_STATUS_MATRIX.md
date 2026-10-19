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
| PDRM-09 migrations | Alembic 31t + 31b two-head unresolved, P0 |
| PDRM-10 interfaces | actual DTO/serialization/auth/driver E2E incomplete |
| PDRM-11 security | no issuer/grant inferred from Skill/docs; production Native hold |
| PDRM-12 verification | PG17 narrow green is not alpha full-suite proof |
| PDRM-13 cross-document | old ledgers are correctly historical, NOT a current conflict |
| PDRM-14 traceability | successor issues #649–#656, specific child issues pending |
| PDRM-15 authority | doc-only normalization authorized; source/test edits NOT authorized at S00 |
| PDRM-16 closure | task, documentation, software readiness statuses reported separately |

## Current doc→code reconciliation
- Source in se/src/main.py bootstraps DirectChatRuntime and AgentRuntime; se/src/domain/schemas/request.py chooses DIRECT by default. Treat Agent-only roadmap as PLANNED, preserve compatibility.
- Old docs referencing former Policy #85 as current must be interpreted with the repository's retirement banner; no revival or tool access grant.
- Alembic migration 31t and 31b are distinct heads: operational docs MUST NOT recommend general upgrade head on live data pending S02.
- Native negative receipt guards are CODE_PRESENT and not genuine OS-issued permission. Full OS proof UNVERIFIED.
- Current issue inventory has superseded historic issues; closed historical issue ≠ technical PASS.
- No asserted changes to old specification facts merely to reconcile current code.

## Suggested future documentation maintenance (not authorized code changes)
Add exact source revision, technical owner, review date, status of activation, ABI/interface version, test evidence link and replacement pointer whenever a stage is actually verified. Preserve historical URLs and old RED material. Keep only one entrypoint describing present development state; stage proposals retain their own scope.

## Scope and restrictions
This is a planning and traceability overlay. The human Owner's procedural review exception preserved from historical #560 does not waive OS physical/security tests or GitHub gates. No deployment or main merge is authorized. Changes beyond docs require fresh bounded source request/approval.
