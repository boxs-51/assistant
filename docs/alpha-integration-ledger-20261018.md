# Non-production Alpha integration ledger — 18 October 2026

## Purpose and scope

This is an **experimental development assembly**, not a release candidate, validation certificate, or production branch. The human Owner requested combining independent development work **including known bugs/RED tests** while preserving **every original issue, source PR, unresolved P0/P1, test oracle, and technical/security HOLD**. Do **not** merge this alpha into `main` or deploy it merely because a source branch was mechanically merged.

- Repository: `boxs-51/assistant`
- Original `main` at alpha creation: `858004fae17732f3b388383c28146c12b36ecdb9` (remains unchanged when this ledger was created)
- Alpha integration snapshot **before this ledger commit**: `2b8857c37e2f5b6a5e82bd1d0c1e82cc7652c93b`
- 93 original open PRs were discovered at initial inventory; many are ancestor/descendant stacks, not 93 independent changes.
- **26 integration PRs MERGED, 14 conflict/pending PRs retained as DRAFT** in the alpha-only queue. These are new temporary PRs; the original issues/PRs were not closed or retargeted.
- Alpha HEAD may fail CI or require physical Redis, PostgreSQL17, SQLite FK matrix, real Windows native proofs, and Linux/Windows tests; failures are NOT auto-exempted from security review or production deployment.

## Exact original-to-alpha integration mapping

| Original PR | Alpha-only PR | Result | Source HEAD |
|---|---|---|---|
| [#597](https://github.com/boxs-51/assistant/pull/597) | [#598](https://github.com/boxs-51/assistant/pull/598) | MERGED INTO ALPHA | `e7fb2d0eccf4` |
| [#556](https://github.com/boxs-51/assistant/pull/556) | [#599](https://github.com/boxs-51/assistant/pull/599) | MERGED INTO ALPHA | `4abfb2b4ea17` |
| [#517](https://github.com/boxs-51/assistant/pull/517) | [#600](https://github.com/boxs-51/assistant/pull/600) | MERGED INTO ALPHA | `d438c686dd91` |
| [#429](https://github.com/boxs-51/assistant/pull/429) | [#601](https://github.com/boxs-51/assistant/pull/601) | MERGED INTO ALPHA | `3e339eb5e077` |
| [#435](https://github.com/boxs-51/assistant/pull/435) | [#602](https://github.com/boxs-51/assistant/pull/602) | MERGED INTO ALPHA | `1fbcbc48087a` |
| [#543](https://github.com/boxs-51/assistant/pull/543) | [#603](https://github.com/boxs-51/assistant/pull/603) | MERGED INTO ALPHA | `9dbfd3c40e4d` |
| [#542](https://github.com/boxs-51/assistant/pull/542) | [#604](https://github.com/boxs-51/assistant/pull/604) | BLOCKED / DRAFT | `9bfdfe9f5919` |
| [#544](https://github.com/boxs-51/assistant/pull/544) | [#605](https://github.com/boxs-51/assistant/pull/605) | MERGED INTO ALPHA | `98771904549c` |
| [#539](https://github.com/boxs-51/assistant/pull/539) | [#606](https://github.com/boxs-51/assistant/pull/606) | MERGED INTO ALPHA | `d7b19b9dff47` |
| [#580](https://github.com/boxs-51/assistant/pull/580) | [#607](https://github.com/boxs-51/assistant/pull/607) | MERGED INTO ALPHA | `f27205a37b19` |
| [#582](https://github.com/boxs-51/assistant/pull/582) | [#608](https://github.com/boxs-51/assistant/pull/608) | MERGED INTO ALPHA | `e2479edcaba8` |
| [#583](https://github.com/boxs-51/assistant/pull/583) | [#609](https://github.com/boxs-51/assistant/pull/609) | BLOCKED / DRAFT | `8f847ee754f0` |
| [#508](https://github.com/boxs-51/assistant/pull/508) | [#610](https://github.com/boxs-51/assistant/pull/610) | MERGED INTO ALPHA | `6b10e820e205` |
| [#534](https://github.com/boxs-51/assistant/pull/534) | [#611](https://github.com/boxs-51/assistant/pull/611) | MERGED INTO ALPHA | `e6f96eccb4e1` |
| [#579](https://github.com/boxs-51/assistant/pull/579) | [#612](https://github.com/boxs-51/assistant/pull/612) | BLOCKED / DRAFT | `cdf709c78ab3` |
| [#555](https://github.com/boxs-51/assistant/pull/555) | [#613](https://github.com/boxs-51/assistant/pull/613) | BLOCKED / DRAFT | `615ee9482d50` |
| [#549](https://github.com/boxs-51/assistant/pull/549) | [#614](https://github.com/boxs-51/assistant/pull/614) | BLOCKED / DRAFT | `8f5659b4f4f9` |
| [#521](https://github.com/boxs-51/assistant/pull/521) | [#615](https://github.com/boxs-51/assistant/pull/615) | MERGED INTO ALPHA | `cb201d7d5624` |
| [#524](https://github.com/boxs-51/assistant/pull/524) | [#616](https://github.com/boxs-51/assistant/pull/616) | MERGED INTO ALPHA | `8340642d4634` |
| [#488](https://github.com/boxs-51/assistant/pull/488) | [#617](https://github.com/boxs-51/assistant/pull/617) | MERGED INTO ALPHA | `30a82a14d7b4` |
| [#482](https://github.com/boxs-51/assistant/pull/482) | [#618](https://github.com/boxs-51/assistant/pull/618) | MERGED INTO ALPHA | `898436f8d38e` |
| [#464](https://github.com/boxs-51/assistant/pull/464) | [#619](https://github.com/boxs-51/assistant/pull/619) | MERGED INTO ALPHA | `2fccc0d5f5ab` |
| [#499](https://github.com/boxs-51/assistant/pull/499) | [#620](https://github.com/boxs-51/assistant/pull/620) | MERGED INTO ALPHA | `2c86f4d2159a` |
| [#497](https://github.com/boxs-51/assistant/pull/497) | [#621](https://github.com/boxs-51/assistant/pull/621) | MERGED INTO ALPHA | `2f0088f1f6a6` |
| [#514](https://github.com/boxs-51/assistant/pull/514) | [#622](https://github.com/boxs-51/assistant/pull/622) | MERGED INTO ALPHA | `d67604609d74` |
| [#487](https://github.com/boxs-51/assistant/pull/487) | [#623](https://github.com/boxs-51/assistant/pull/623) | BLOCKED / DRAFT | `fb925cbdf814` |
| [#484](https://github.com/boxs-51/assistant/pull/484) | [#624](https://github.com/boxs-51/assistant/pull/624) | MERGED INTO ALPHA | `0e40439228f8` |
| [#483](https://github.com/boxs-51/assistant/pull/483) | [#625](https://github.com/boxs-51/assistant/pull/625) | MERGED INTO ALPHA | `20fd7c100480` |
| [#478](https://github.com/boxs-51/assistant/pull/478) | [#626](https://github.com/boxs-51/assistant/pull/626) | MERGED INTO ALPHA | `fb537f4fe1e6` |
| [#465](https://github.com/boxs-51/assistant/pull/465) | [#627](https://github.com/boxs-51/assistant/pull/627) | BLOCKED / DRAFT | `41b46411d8f4` |
| [#512](https://github.com/boxs-51/assistant/pull/512) | [#628](https://github.com/boxs-51/assistant/pull/628) | MERGED INTO ALPHA | `aaa2b7a06e76` |
| [#503](https://github.com/boxs-51/assistant/pull/503) | [#629](https://github.com/boxs-51/assistant/pull/629) | BLOCKED / DRAFT | `3c8afbddede7` |
| [#505](https://github.com/boxs-51/assistant/pull/505) | [#630](https://github.com/boxs-51/assistant/pull/630) | BLOCKED / DRAFT | `fe608900691c` |
| [#502](https://github.com/boxs-51/assistant/pull/502) | [#631](https://github.com/boxs-51/assistant/pull/631) | MERGED INTO ALPHA | `ff39f9d5074f` |
| [#494](https://github.com/boxs-51/assistant/pull/494) | [#632](https://github.com/boxs-51/assistant/pull/632) | BLOCKED / DRAFT | `f7efcd63c479` |
| [#493](https://github.com/boxs-51/assistant/pull/493) | [#633](https://github.com/boxs-51/assistant/pull/633) | BLOCKED / DRAFT | `ab6c04ee9b39` |
| [#491](https://github.com/boxs-51/assistant/pull/491) | [#634](https://github.com/boxs-51/assistant/pull/634) | BLOCKED / DRAFT | `ad250e45ccf0` |
| [#576](https://github.com/boxs-51/assistant/pull/576) | [#635](https://github.com/boxs-51/assistant/pull/635) | MERGED INTO ALPHA | `88be1e5ad88a` |
| [#547](https://github.com/boxs-51/assistant/pull/547) | [#636](https://github.com/boxs-51/assistant/pull/636) | BLOCKED / DRAFT | `aadd5a419390` |
| [#537](https://github.com/boxs-51/assistant/pull/537) | [#637](https://github.com/boxs-51/assistant/pull/637) | BLOCKED / DRAFT | `aec33975d40d` |

## Three original leaf branches not independently staged

- **#581**: GitHub returned `422 No commits between alpha and fix/sbx3-417-ci-oracle-reconnect-20261016`; source ancestry already included through a merged composite.
- **#525**: GitHub returned `422 No commits between alpha and evidence/164-local-offline-agent-host-spawn-red-20261014`; source ancestry already included through a merged composite.
- **#516**: its strict Linux/Windows workflow delta had been verified byte-identical to successor #597 and was included through merged alpha-only #598. Original #516 stays unchanged.

## Unresolved integration queue

- [Alpha PR #604](https://github.com/boxs-51/assistant/pull/604) (source #542): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #609](https://github.com/boxs-51/assistant/pull/609) (source #583): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #612](https://github.com/boxs-51/assistant/pull/612) (source #579): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #613](https://github.com/boxs-51/assistant/pull/613) (source #555): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #614](https://github.com/boxs-51/assistant/pull/614) (source #549): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #623](https://github.com/boxs-51/assistant/pull/623) (source #487): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #627](https://github.com/boxs-51/assistant/pull/627) (source #465): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #629](https://github.com/boxs-51/assistant/pull/629) (source #503): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #630](https://github.com/boxs-51/assistant/pull/630) (source #505): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #632](https://github.com/boxs-51/assistant/pull/632) (source #494): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #633](https://github.com/boxs-51/assistant/pull/633) (source #493): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #634](https://github.com/boxs-51/assistant/pull/634) (source #491): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #636](https://github.com/boxs-51/assistant/pull/636) (source #547): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.
- [Alpha PR #637](https://github.com/boxs-51/assistant/pull/637) (source #537): DRAFT; three-way source integration was blocked by conflict or GitHub mergeability not finalized. Do not force-resolve or overwrite protected evidence.

## Hard safety boundaries retained

1. `NO_DEPLOY=YES`; never run production migrations, service restarts, deployments, or automated alpha-to-main promotion.
2. `SECURITY_HOLD=PRESERVED`: AE359 Redis/PG17 ACK-loss durability, OTP atomicity and refresh/logout fences; SBX3 native OS custody, C2 and signed-origin R7/R12; CLI/direct effect one-use receipt; CTX/CAS/UBQ owner/account integrity; AIC31a/31b/PG17 migration guards and rollback/HA evidence. Inclusion in alpha **does not** mean these gates passed.
3. Do not relax tests, remove existing `skip/xfail` or assert that intentionally RED evidence is green.
4. Do not close/rename/change issue statuses, force-push original branches, or mark original PRs merged.
5. To resolve each blocked integration, compare the exact conflicting source paths against alpha and canonical architecture; add RED-first regression where appropriate, then create a **new non-production branch from the current alpha** and use its reviewed fix. Do not overwrite newer code with older full-file snapshots.
6. Verify fresh exact-tree Linux/Windows CI and independently review semantic security boundaries before any future main-target proposal.

**This file records integration custody, not a claim of functional correctness.**


## Owner continuation checkpoint — 2026-10-18 (after initial ledger)

This section is **additional evidence**, not a retroactive change to the 26-merge/14-queue original snapshot.

- Original baseline `main@858004fae17732f3b388383c28146c12b36ecdb9` was verified unchanged at integration start.
- `alpha` at original ledger commit: `dbc58f22e9c4c36176a5355838c19187c6f3996a`.
- [Alpha PR #604](https://github.com/boxs-51/assistant/pull/604) (original #542) subsequently **MERGED** as `1c9715ffd0796472c8fe7acdbaba4191fb7fe993`: exactly three application-layer CAS RED-first test files, no runtime source modifications. Tests may intentionally fail; this does not waive CAS integrity/security HOLD.
- [Alpha PR #638](https://github.com/boxs-51/assistant/pull/638) subsequently **MERGED** as `aed7a90bd10d25887c0b9e5234d107dbccfd91d1`, adding **six previously absent** Windows Job B0 / D2 recovery source, tests and two-OS evidence files from the separate #623/#627 development branches; comparison against the integration parent verified exactly six ADDED paths, zero overwritten paths. This is only a **partial** recovery of original #487/#465: both conflicting stacked PRs remain DRAFT and unmerged; no live runtime/authority path was modified by #638. No new Native/Agent permissions are authorized.
- The integration **now contains 28 completed alpha-only merge PRs**, counting the original 26, #604 and #638. **13 original alpha-stage conflict PRs remain open/DRAFT:** #609, #612, #613, #614, #623, #627, #629, #630, #632, #633, #634, #636, #637. They must not be described as fully integrated, reviewed, tested, or resolved.
- At exact #604 parent tree, Git blob comparison showed source-PR path identity with alpha for much of the blocked work: #609 16/22 identical, #612 33/37 identical, #613 4/6, #614 19/25, #629 2/3, #630 2/3, #636 1/3, #637 1/5. The remaining path differences are **semantic conflicts**, not evidence of missing branch history. Do not overwrite current alpha files with old full-file snapshots or adopt an `ours` merge that silently hides the conflicting source code.
- Other open original PRs and all original issues remain unchanged by these new actions. Source PRs may continue to report intentionally RED CI. No source branch was force-pushed, deleted, or retargeted.
- The user requested a new development successor branch from the final alpha HEAD; that new branch must be pinned to the final ledger commit, not the pre-ledger SHA. See live GitHub branch refs for exact identity.
- This is **not a release-readiness certification**: no exact-tree comprehensive alpha Linux/Windows/Redis/PG17 proof has been published for the final merged tree, no independent security approval is claimed. Alpha contains intentionally RED evidence and unresolved conflicts.
- **SECURITY_HOLD=PRESERVED; NO_DEPLOY=YES; NO_MAIN_MERGE=YES; NO_ISSUE_STATUS_CHANGE=YES.**
