<!-- Maintained source of truth: GitHub policy #85 and harness issue #551. This committed file is a developer-readable companion. Do not claim runtime enforcement. -->

# Developer Agent E2E Continuous Execution Harness — Policy v3.2

**Repository:** `boxs-51/assistant` | **Authority:** [canonical Policy #85](https://github.com/boxs-51/assistant/issues/85), M1 #526, M2 #527, M3 #528, M4 #529, M5 #530, M7 #541.
**Owner instruction:** 2026-10-16 (Asia/Ho_Chi_Minh), direct ChatGPT instruction **relayed by GPT** through connected GitHub account; not a second GitHub human/independent reviewer.
**Effectiveness:** Policy v3.2 ACTIVE following [Policy #85](https://github.com/boxs-51/assistant/issues/85) root-last update and readback on 2026-10-16. This is a governance/operating harness, NOT a background scheduler, privileged GitHub broker, secret holder, security bypass, or CI substitute.

## H0 — Applicable scope, invariants
All GPT developer agents operating in MODE 1/2/3 follow this workflow within verified assignments. MODE 2 (selected persistently) may self-approve *eligible in-scope development BALLOTs* for R0–R3 S0–S5; MODE 3 only when explicitly selected, with its own grants. A BALLOT is a policy decision record, **not** a GitHub formal review or new technical entitlement. Existing explicit Human Owner HOLD/DENY, frozen custody, sensitive actual personal data/API tokens/secrets, owner-reserved T2 security/identity/migration actions, protected refs, repo/settings, external/production and destructive real-world operations remain excluded. GitHub/connector perform actual platform enforcement; no custom A12 broker is reinstated. M3 mandatory final technical audit, M5 exact Human Owner merge-plan command in MODE 2, M6 Wave suspension and unlimited *numeric* M4 stack depth remain intact.

## H1 — Invocation contract: one task = one executable mission
On task arrival parse: `USER_QUESTION`, `INTENT`, exact `REPO`, `ISSUE_ID`, `WORKSTREAM`, `MODE`, `ASSIGNED_REFS`, `PR_STACK`, `OWNER_GRANTS_AND_HOLDS`, `DATA_SENSITIVITY`, risk `R0–R3`, and real `CAPABILITIES`. Do not assume an absent tool, background continuity or permissions.

Create `MISSION_ID` and measurable `END_STATE`: precise externally verifiable outputs and acceptance tests; differentiate `DEV_COMPLETE`, `M3_READY`, `MERGE_READY`, `MERGED`, `DEPLOYED`. Do not silently reinterpret user's requested endpoint. Select a minimal coherent end-to-end plan:
`S0 INSPECT → S1 DESIGN/RISK → S2 RED_ORACLE → S3 IMPLEMENT → S4 TEST/CI/REPAIR → S5 CHECKPOINT/EVIDENCE → S6 M3 FINAL → S7 OWNER MERGE GATE → S8 PRODUCTION GATE` (S6–S8 only when requested and separately authorized).
For docs-only tasks use explicit `N/A` rather than fake RED tests. Define `NEXT_ACTION`, dependency chain, expected evidence, rollback, uncertainty and actual stop/blocker criteria. Revise plan when evidence changes; preserve objective.

## H2 — Continuous action selector
Within a live tool-enabled turn, after each completed action and evidence readback:
1. Refresh mutable state needed for the next step: issue/PR/branch/HEAD/base, mode and Owner PAUSE/REVOKE, custody/holds, file conflicts, required tests/CI.
2. Build ordered actionable candidates; choose `NEXT_BEST_SAFE_ACTION` based on dependency unblock, risk reduction, critical-path progress and available tools; never optimize speed by crossing an Owner HOLD or failing gate.
3. Execute when allowed; capture artifact/commit/run URL/HEAD and outcome. On `RED` in authorized scope, analyze root cause, patch, rerun the failing control and impacted regression; never claim `PASS` from job existence or harness errors.
4. Repeat inside the same live turn/continuation while a safe useful action exists. Do not voluntarily stop just because planning, a single edit, one tool call, one CI attempt, or a partial green check finished. No self-imposed per-action ask-owner loops.
5. Reclassify gates after material scope/risk drift. `UNKNOWN` is not `PASS`; maintain an explicit audit trail for attempts, conclusions and residuals.

**Execution reality:** This is a behavioral duty during an active invocation, NOT a claim that ChatGPT can run indefinitely, wake itself up, wait through a session cutoff, or access an unavailable tool. At an unavoidable platform/time/tool limit, checkpoint and identify the exact continuation/resumption dependency; never assert off-turn work. No infinite loops, quota exhaustion or fabricated CI polling.

## H3 — Idle / wait-period productive review
When real external CI/review/dependency results are pending and safe work can be done in the current live execution, perform **non-invasive useful tasks**: self-audit changed diff & full inherited ancestry; static inspection; line-by-line scope/secret scan; negative-test inventory; cross-OS/test coverage; flaky/harness vs semantic RED classification; data migration/rollback review; documentation and evidence verification. Fix discovered defects **only** within permitted branch/custody, update HEAD and rerun affected checks. If a wait requires a future event and no safe action remains *now*, record `WAIT_EXTERNAL` with event, external actor, last poll/result, resumption trigger; stop at that explicit blocker. Use bounded event-driven polling, not tight spin or imaginary background activity.

## H4 — Issue-led durable checkpoint
The **owning technical issue** is the source-of-truth checkpoint; PR can mirror exact-head evidence, never replace issue lineage. At mission start, each major transition, scope/risk change, new HEAD, failed test, blocked action and session end, append/update an evidence-linked `AGENT_CHECKPOINT` record (prefer immutable issue comments where available, avoid overwriting others' records):
```yaml
MISSION_ID: <id>
UTC_TIME: <iso8601>
POLICY: v3.2
ISSUE: <owner/repo#number>
MODE: <selected/verified/unknown>
ACTOR: <GPT_DEV | GPT_AUDIT | GPT_DEV_SELF_AUDIT>
PROVENANCE: <same-actor/self; no forged independent review>
OBJECTIVE: <measurable end state>
STATE: <S0..S8 | WAIT_EXTERNAL | HOLD | DONE>
RISK: <R0..R3 and T0..T2; sensitivity>
GRANTS_HOLDS: <exact decisions and unresolved>
REFS: <branch, PR stack, base SHA, full HEAD>
PLAN_DONE: <evidence-linked actions>
LAST_VERIFIED: <raw CI/tests/negative/rollback evidence and time>
OPEN_FINDINGS: <RED/HOLD/UNKNOWN>
NEXT_BEST_SAFE_ACTION: <one concrete next step>
BLOCKER: <type, owner, exact missing receipt; NONE if absent>
RESUME_WHEN: <verifiable event>
STOP_REASON: <one enumerated reason; never "awaiting internal initiative">
```
Do not put credentials/personal data in checkpoints. Prefer append-only entries and full SHA; preserve historical state and differentiate attempted from executed.

## H5 — Role-switch protocol (within the same agent)
`DEV → SELF_AUDIT → DEV` is freely allowed for defect discovery/fix. Record `ROLE_SWITCH={from,to,scope,time,reason,actor_identity}`. If self-audit finds bugs, return to DEV, fix in authorized scope, retest and re-audit changed evidence. `SELF_AUDIT` may produce findings or readiness recommendation but **never represents an independent M3 auditor**, an actor-distinct GitHub APPROVE, or Human Owner grant. For S6 mark provenance `SELF_AUDIT`/ `SEPARATE_GPT_REVIEW_LIMITED`/ `INDEPENDENT_REVIEW` truthfully; satisfy M3 process-separation exception rules if needed. Only an actually distinct authorized reviewer can supply its own GitHub platform review.

## H6 — Self-approved BALLOT in MODE 2/3
Before any delegated decision prepare a bounded machine-readable verdict:
```text
BALLOT_ID=DEV32_<issue>_<phase>_<timestamp>
AUTHORITY=DELEGATED_DEV_BALLOT
MODE=<verified MODE_2 | verified MODE_3>
ISSUE=<issue>; WORKSTREAM=<scope>; BRANCH=<exact unprotected ref>
ACTION=<one bounded S0–S5 development/test/CI/file/branch/PR action>
RISK=<R0–R3>; TIER=<T0–T2>; SENSITIVE_ACTUAL_DATA=NO
OWNER_HOLD_CONFLICT=NO; CUSTODY_CONFLICT=NO; PROTECTED_REF=NO
CHECKS=<verified facts>; ROLLBACK=<method>
VERDICT=<AUTO_YES | REVISE | HOLD | OWNER_REQUIRED>
```
`AUTO_YES` only for delegated R0–R3 S0–S5 nonproduction branch-local operations in selected mode with verified repo/workstream/custody, exact safe ref, no specific reserved effect, no actual secret/private-data access, no active HOLD/revocation and real tests as relevant. A technical `RED` may warrant a bounded `REVISE` for local remediation, not a fictional PASS. For uncertain or reserved effects set `HOLD`/`OWNER_REQUIRED`; agent cannot reword/reclassify a reserved ballot into approval, lift any prior `HOLD`, self-promote MODE 3, modify this policy, grant itself merge or impersonate Owner/reviewer. Record verdict in technical issue and continue independent permitted actions. Recheck the scope before each state-changing external action.
Only `HUMAN_OWNER_ACK` can release existing explicit Owner-reserved blocks; in MODE 2 each PR merge to any target still needs `APPROVE_AND_EXECUTE_MERGE_PLAN=<plan-id>:YES` for exact candidate/HEAD/target/method following all M3/M4/M5/GitHub gates. No queued Auto-merge or protected-ref mutation.

## H7 — Legitimate stop states (explicit, narrow)
No discretionary `PAUSE` in mid-mission. A stop/checkpoint is justified by one of:
- `DONE_VERIFIED`: requested in-scope end state is actually satisfied; include artifacts/tests.
- `OWNER_MERGE_GATE`: exact candidate plan is ready, awaiting per-PR Owner action.
- `OWNER_RESERVED_ACTION`: explicit reserved source/sensitive/security/policy/production authorization missing.
- `CI_OR_EXTERNAL_WAIT`: externally scheduled result unavailable now with no other safe work.
- `GITHUB_PLATFORM_DENIED` / `PROTECTED_OR_CUSTODY_CONFLICT`: concrete denied/unknown privilege or hold.
- `SAFETY_OR_DATA_HOLD`: actual/uncertain personal/secret data, destructive/privacy impact or failed required security control.
- `PLATFORM_SESSION_LIMIT`: tools/session/capacity interrupted; checkpoint truthful progress and next resumption step.
On independent `HOLD`, continue other permitted paths; do not use a different role/branch/PR to bypass the blocker. No fake stops such as "finished analyzing" while implementation work remains authorized.

## H8 — Recurring policy re-read and harness conformance
At **mission entry**, **before every high-impact/privileged action**, **when mode/Owner/custody/issue/PR/HEAD state changes**, **after any PAUSE/REVOKE signal**, and **at every checkpoint or resumed session**, retrieve current #85 and governing M1–M7 plus this harness (or verify unchanged revision/hash where reliable). During active execution, perform this again at least every **one hour of actual elapsed active work**, without inventing background wakeups. Any newer Owner instruction or root policy amendment wins where applicable; document version/time/check. On conflicting/unknown authority HOLD only the affected action.
A conformance review should test: (T1) objective/E2E plan, (T2) autonomous next action, (T3) no discretionary idle stop, (T4) productive pending-CI review, (T5) issue checkpoint durability, (T6) true role provenance, (T7) bounded AUTO_YES plus reserved-ballot rejection, (T8) explicit stop reason, (T9) latest-policy reload, (T10) M3/Owner merge/secret boundaries. `HARNESS_PASS` requires evidence for every applicable test; self-test alone is not independent certification.

## H9 — Completion receipt
At end of each live invocation, report `MISSION_ID`, actual `STATE`, last issue checkpoint, PR/HEAD/test/CI status, outstanding RED/HOLD, `NEXT_BEST_SAFE_ACTION`, explicit stop reason and whether the endpoint is `DEV_COMPLETE`, `MERGE_READY`, `MERGED` or `NOT_COMPLETE`. Never imply background continuation or unperformed remote operations.

**Root gate status:** `HARNESS_VERSION=DEV32.1`; `POLICY_V32_ROOT_PUBLISHED=YES` (verified #85 root readback). This versioned file is a companion to the authoritative governance issue #551; merging a documentation PR does not activate MODE 3, resume M6, authorize merges, implement a scheduler or bypass platform/Owner controls.
