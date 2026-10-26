# S04 OTP — evidence/status reconciliation (2026-10-26)

**Classification:** non-production alpha documentation, evidence-custody only.
**Issues:** [#661](https://github.com/boxs-51/assistant/issues/661), [#650](https://github.com/boxs-51/assistant/issues/650), [#655](https://github.com/boxs-51/assistant/issues/655), [#656](https://github.com/boxs-51/assistant/issues/656).
**Documentation base:** `alpha@ee2a71c5cbdce1aed2431fed62f05f7cf79e8ddb`.
This ledger does **not** claim that the unmerged PR source is present on `alpha`.

## Exact independent observations

| Scope | Source / workflow | Observed result | Interpretation |
|---|---|---|---|
| Legacy-first Redis + cold Redis modern write | [Draft PR #670](https://github.com/boxs-51/assistant/pull/670) @ `4eb7c49a52ce3997ee3075666f9def6cebbf2353` | Historical 28/28 checks successful (2026-10-21) | **PASS_SCOPED_PR_CI**, not mixed-rollout/release PASS |
| Cross-backend mixed legacy/new OTP | [Draft PR #671](https://github.com/boxs-51/assistant/pull/671) @ `b20a925b8dae419d886c6f7c017c6c040df5d2ab` | 42/43 checks successful; [one intentionally RED check](https://github.com/boxs-51/assistant/actions/runs/39893780222) (2026-10-23) | **BLOCKED_P1**. Six semantic failures, not infrastructure errors |
| Windows NTFS + synthetic old-process SQLite handle | [PR #671 T12 job](https://github.com/boxs-51/assistant/actions/runs/39893779141) at the same `b20a925b` | 6/6 physical selectors PASS; [raw artifact 12476455238](https://github.com/boxs-51/assistant/actions/runs/39893779141) | **PASS_SCOPED_HAZARD_REPRODUCTION**, not application protection |

The six failing mixed-authority cases are registration and password reset for each:
1. A legacy Redis challenge coexisting with a new SQL-only issuer produces two independently redeemable OTPs.
2. An old unconditional Redis `SET` can replace a modern acknowledged `SET NX` OTP.
3. Redis legacy and modern process-local InMemory issuers can both redeem OTPs for the same principal/purpose.

Four isolated SQL-only/InMemory controls pass; disabling healthy isolated modes solely to make the negative matrix green is **not** a valid remedy. The [mixed physical job](https://github.com/boxs-51/assistant/actions/runs/39893780222) reported all six `S04_MIXED_SEMANTIC_RED` assertions at `b20a925b`.

T12's six Windows selectors (new write, stale restore and previously opened handle, each for both purposes) passed on an isolated GitHub-hosted Windows 2025 runner. The **negative held-handle controls** demonstrated an already-running synthetic old process could drop a SQLite trigger and delete a row through a writable handle opened before NTFS revocation. This is not a shipped old application, nor evidence of deployed protection; the result makes **process/handle drainage** a required cutover acceptance condition. The physical test documents its limited `PRAGMA journal_mode=MEMORY` fixture in [S04 T12 contract](S04_T12_WINDOWS_NTFS_OLD_HANDLE.md).

## Open stage gates (not satisfied by these results)

- **Trusted issuer ownership:** external, independently attested exclusive writer epoch/authority and inventory of every historical writer identity; PR-controlled flags/receipts cannot grant authority.
- **Historical compatibility:** authentic old-version application/verifier binaries, independently credentialed Redis writers or a proven stop/drain barrier, and negative verification for shared privileged credentials.
- **Crash/recovery/cutover:** OTP payload persistence/restart (T12/ACL tests do not establish it), Redis/PG17 session refresh/logout linearization, expiry/reissue, failover, downgrade/rollback while an OTP remains live, and old open handles.
- **Integration/security evidence:** exact-candidate full `alpha` Linux/Windows/Redis/PG17/SQLite regression, original positive and negative oracle identities, independent review and release gate [#665](https://github.com/boxs-51/assistant/issues/665).

**Decision:** `S04=HOLD/BLOCKED_P1`; `S05_PROMOTION=NOT_AUTHORIZED`; `PR670/671=DRAFT`; **NO_READY / NO_MERGE / NO_DEPLOY**. A job whose purpose is to preserve semantic RED must not be reclassified as a passing release check. No production changes, security oracle changes, rollout permission changes, or review claims are made by this document.

## Next bounded engineering sequence

1. Preserve the 10-case mixed-authority RED/positive matrix and the T12 old-handle negative controls as immutable acceptance oracles.
2. Specify externally trusted old-writer drain/termination, Redis credential separation and exclusive authority per `principal × purpose`, including SQL-only/InMemory routing. Require explicit deny on unknown epoch, leader, existing handle or writer inventory.
3. Reproduce the candidate protocol with genuine old+new application binaries, persisted payloads, restart/failover and adverse rollback on a disposable physical runner. Record exact SHA and raw JUnit; never claim protection from synthetic-only controls.
4. Only after the RED-first physical safety boundary is satisfied, scope the smallest runtime change and independently review it. Until then, preserve S04 P1 HOLD and do not advance dependent S05–S09 source promotion.

Links and counts above describe the exact previously observed commits; this documentation commit is **not** a new CI pass.
