# TV1-T9 Post-Cleanup Main Reconciliation

Repository: boxs-51/assistant
Issue: #12
Date: 2026-09-23

## Authority

Re-anchor base:

```text
main @ 3560a2d034d25659aa535ba91b90aa48256f5ac1
```

Historical accepted Tools planning authority:

```text
tools-v1-contract-freeze @ 1ae243a76bfda26628a348d3aad31592b649ee8c
TV1-T9-A freeze @ ce9704638257fb83fedcf6062462d2f1a74e4efe
```

## Reconciliation rule

The historical Tools line is not merged wholesale.

The replay copies only the 66 paths changed by the accepted Tools line relative
to its merge-base `a55e4fd2a20ddccbd227e770d26fae72e33bc88e`, plus the T9-A freeze document, onto current main.

A merge-base audit found:

```text
current-main changed paths: 134
accepted-Tools changed paths: 66
exact changed-path overlap: 0
```

Therefore no Agent Execution, provider/PTC, SQL, Central Asset, or current-main
file is replaced because of a same-path historical conflict.

This does not by itself prove semantic compatibility. TV1-T9-B remains blocked
until the reconciled HEAD passes the T8 convergence/regression gates.

## Production state

```text
TV1-T8 authority: replayed onto post-cleanup main
TV1-T9-A: replayed / docs-only
TV1-T9-B: NOT CLAIMED / NOT STARTED
```

No T9 logical-export migration is included in this reconciliation commit.

## Required gate before T9-B

1. audit changed-file scope against current main;
2. run T8 Metadata V2 convergence tests;
3. run local tool loader / client registration regressions;
4. run full PR CI on Linux + Windows client contracts;
5. re-check Issue #12 for auditor findings;
6. only then claim TV1-T9-B.
