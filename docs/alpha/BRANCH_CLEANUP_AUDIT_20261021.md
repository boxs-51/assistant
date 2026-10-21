# Alpha branch cleanup: ancestry and PR dependency custody — 2026-10-21

**Immutable comparison base:** `alpha@300801d67c43d702408d2895f7c89898e27a87fd`. Snapshot: **197 current branches**, **432 PR records** across every paginated historical PR page, **0 open PRs**. This is a **conservative, partial ancestry audit**: only five candidate refs have commit-graph comparison proofs, and all unverified refs are marked PRESERVE.

## Authorized scope and denial
- Owner authorized **conditional deletion** only of provably unneeded refs using a GitHub deletion operation that the connector permits. The available GitHub connector has no authorized `DELETE git/refs/heads` mutation; Computer Bridge terminal is under SECURITY_HOLD and is prohibited as a workaround. **Deletion count: 0.**
- A name such as `tmp/`, `fix/`, `test/`, `evidence/` or `dev/` does **not** imply deletability. Do not delete a branch with unmerged ahead commits, open PR dependencies, historical PR base custody, a protected/core/archival function, or missing ancestry proof.
- `DELETE_CANDIDATE_ANCESTRY_PROVEN` means base→tip comparison ahead=0, no open PR and no PR-base custody; still requires fresh remote tip check, alternate child-branch dependency audit and an authorized GitHub ref-delete method before actual deletion.
- `PRESERVE_PENDING_ANCESTRY` denotes conservative hold, **not** that the branch is useful or unused. A future authorized follow-up can compare the remaining refs incrementally and reclassify them.

## Summary
| Decision | Count |
|---|---:|
| `DELETE_CANDIDATE_ANCESTRY_PROVEN` | 1 |
| `PRESERVE_CORE` | 9 |
| `PRESERVE_HISTORIC_PR_BASE` | 63 |
| `PRESERVE_PENDING_ANCESTRY` | 121 |
| `PRESERVE_UNMERGED_COMMITS` | 3 |

## Verified high-priority five ref comparisons
| Branch | Ahead | Behind | PR head links | Outcome |
|---|---:|---:|---|---|
| `dev/alpha-integration-hardening-20261018` | 0 | 104 | — | `DELETE_CANDIDATE_ANCESTRY_PROVEN` |
| `dev/alpha-s01-evidence-baseline-20261019` | 1 | 68 | #666 | `PRESERVE_UNMERGED_COMMITS` |
| `dev/alpha-consolidated-next-20261018` | 4 | 97 | — | `PRESERVE_UNMERGED_COMMITS` |
| `dev/alpha-rack07-native-d2-composition-20261019` | 1 | 94 | — | `PRESERVE_UNMERGED_COMMITS` |
| `fix/alpha-aic5b-pg17-width-20261018` | 4 | 97 | #639 | `PRESERVE_HISTORIC_PR_BASE` |

## Full current-remote branch registry
| Branch | Tip SHA | Ahead | Behind | Historic PR head/base | Disposition |
|---|---|---:|---:|---|---|
| `alpha` | `300801d67c43` | ? | ? | — B:#668,#667,#666,#648,#647,#646,#645,#642,#641,#639,#638,#637,#636,#635,#634,#633,#632,#631,#630,#629,#628,#627,#626,#625,#624,#623,#622,#621,#620,#619,#618,#617,#616,#615,#614,#613,#612,#611,#610,#609,#608,#607,#606,#605,#604,#603,#602,#601,#600,#599,#598 | `PRESERVE_CORE` |
| `archive/alpha-pre-consolidation-20261019` | `33135f4d0d4f` | ? | ? | — | `PRESERVE_CORE` |
| `audit/359-242-direct-effect-ledger-p0-20261014` | `cb201d7d5624` | ? | ? | H:#615,#521 | `PRESERVE_PENDING_ANCESTRY` |
| `audit/sbx3-main-target-compose-20261016-r1` | `e2479edcaba8` | ? | ? | H:#608,#582 | `PRESERVE_PENDING_ANCESTRY` |
| `audit/sbx3-pytest-skip-evidence-20261017` | `2d2d492f335a` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `audit/sec408-pg-physical-evidence-20261017` | `9506b9fcb643` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `audit/tv1-t11-b-pillow-12-3-0-install-proof-5a75ac14` | `ad226d692933` | ? | ? | H:#296 | `PRESERVE_PENDING_ANCESTRY` |
| `backup/central-asset-storage-pre-r7i` | `e8c2b7130350` | ? | ? | — | `PRESERVE_CORE` |
| `checkpoint/ubq-4-pre-cas-8233b80e` | `8233b80e2efe` | ? | ? | — | `PRESERVE_CORE` |
| `checkpoint/ubq-4-pre-ctx-0e7585bc` | `0e7585bc6de9` | ? | ? | — | `PRESERVE_CORE` |
| `checkpoint/ubq-4-pre-r12-65abc162` | `65abc16242f2` | ? | ? | — | `PRESERVE_CORE` |
| `ci/r9h-p1g-pg-parity-pr439-527239d` | `b325a4553679` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `ci417-a1-expected-red-20261010` | `081a389dce1d` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `ci417-sec408-g3-evidence-20261011` | `ad110b12da1e` | ? | ? | H:#455 | `PRESERVE_PENDING_ANCESTRY` |
| `ctx-f5-3j-reanchor-4b055890-temp` | `65172d0452e9` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `ctx-f5-3j-reanchor-c7eec511-temp` | `7adadc8ac6d8` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `dev/359-main-composed-20261016` | `b6e96537a0de` | ? | ? | H:#554 B:#557 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/359-postack-physical-t1-20261016` | `9c33fd406d14` | ? | ? | H:#557 B:#558 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/359-pr545-t2-refresh-auth-20261015` | `0a98d2ddcd67` | ? | ? | H:#546 B:#548 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/359-pr548-otp-atomic-source-20261016` | `8f5659b4f4f9` | ? | ? | H:#614,#549 | `PRESERVE_PENDING_ANCESTRY` |
| `dev/359-redis-uncertainty-t2-20261016` | `2014e6d9a0f8` | ? | ? | H:#563 B:#579 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/359-t2-utc-naive-pg17-20261016` | `37ea9e7a2440` | ? | ? | H:#561 B:#563 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/359-utc-naive-pg17-t1-20261016` | `b30e6c483273` | ? | ? | H:#558 B:#561 | `PRESERVE_HISTORIC_PR_BASE` |
| `dev/74-cas234-app-guards-20261015` | `f656a30dc33b` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `dev/alpha-consolidated-next-20261018` | `2cd7995340e2` | 4 | 97 | — | `PRESERVE_UNMERGED_COMMITS` |
| `dev/alpha-integration-hardening-20261018` | `dbc58f22e9c4` | 0 | 104 | — | `DELETE_CANDIDATE_ANCESTRY_PROVEN` |
| `dev/alpha-rack07-native-d2-composition-20261019` | `6a4a397ee25d` | 1 | 94 | — | `PRESERVE_UNMERGED_COMMITS` |
| `dev/alpha-s01-evidence-baseline-20261019` | `6d2ffd1b3ecf` | 1 | 68 | H:#666 | `PRESERVE_UNMERGED_COMMITS` |
| `diagnostic/381-426-postgres-alembic-version-table-20261012` | `87fcd0585025` | ? | ? | H:#492 B:#493 | `PRESERVE_HISTORIC_PR_BASE` |
| `diagnostics/416-cas-f6-win-node-isolated-20261011` | `1fbcbc48087a` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `docs/156-426-381-pg17-31b-production-quiescence-20261014` | `d67604609d74` | ? | ? | H:#622,#514 | `PRESERVE_PENDING_ANCESTRY` |
| `docs/381-aic31b-prod-admission-barrier-design-20261014` | `d438c686dd91` | ? | ? | H:#600,#517 | `PRESERVE_PENDING_ANCESTRY` |
| `docs/alpha-post-s03-audit-20261021` | `59f1605ee92b` | ? | ? | — | `PRESERVE_CORE` |
| `docs/apr-c0a-fast-control-e2395dab` | `ef6e4704855c` | ? | ? | H:#280 | `PRESERVE_PENDING_ANCESTRY` |
| `docs/retire-policy85-historical-contracts-20261016` | `4abfb2b4ea17` | ? | ? | H:#599,#556 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/141-ubq-t1-owner-rekey-fkoff-20261014` | `e6f96eccb4e1` | ? | ? | H:#611,#534 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/15-ctx-t1-owner-retention-red-first-20261015` | `9dbfd3c40e4d` | ? | ? | H:#603,#543 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-426-31b-p1-composed-20261013` | `fe608900691c` | ? | ? | H:#630,#505 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-426-aic31b-interrupt-pg17-20261014` | `42485ba8022e` | ? | ? | H:#511 B:#518,#514 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/156-426-aic31b-quiescence-pg17-20261013` | `a75d7df9631e` | ? | ? | H:#509 B:#511 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/156-426-aic31b-unified-pg17-gates-20261014` | `b38c32dc24b4` | ? | ? | H:#520 B:#539,#523 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/156-426-pr494-pr496-pr500-composed-20261013` | `3c8afbddede7` | ? | ? | H:#629,#503 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-426-pr504-nowait-pr494-composed-20261013` | `3e3f34b0892d` | ? | ? | H:#506 B:#509 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/156-aic31b-pg17-composed-20261013` | `ff39f9d5074f` | ? | ? | H:#631,#502 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-aic467-fkoff-id-final-pg17-20261012` | `20fd7c100480` | ? | ? | H:#625,#483 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-aic467-full-legacy-pg17-compat-20261012` | `ad250e45ccf0` | ? | ? | H:#634,#491 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-aic467-id-hardrestart-pg17-20261012` | `8a44f03a6596` | ? | ? | H:#481 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-aic467-legacy-alembic-pg17-20261012` | `c73ae4d5bc83` | ? | ? | H:#489 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/156-aic467-pg17-id-hardexit-20261013` | `868546fdd945` | ? | ? | H:#496 B:#500 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/156-aic467-pg17-linux-windows-20261012` | `e902562722a6` | ? | ? | H:#476 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/164-b0-tools371-private-abi-crosspr-20261012` | `fb537f4fe1e6` | ? | ? | H:#626,#478 B:#479 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/164-local-offline-agent-host-spawn-red-20261014` | `ea2bc0a6af76` | ? | ? | H:#525 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/381-426-5b-pg17-fullchain-20261012` | `ab6c04ee9b39` | ? | ? | H:#633,#493 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/381-d0-real-etcd-r01-r18-20261014` | `8340642d4634` | ? | ? | H:#616,#524 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/400-ci-equivalent-reanchor-stage-only` | `b0472baa048e` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/402-400-windows-combined-nonmerge` | `e12f1d131169` | ? | ? | H:#404 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/408-p1a-v31-r0-2254a828` | `321cc81934a5` | ? | ? | H:#442 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/408-r0-2254a828` | `b99445801839` | ? | ? | H:#444 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/408-r0-54df8cd9` | `867d35d3d440` | ? | ? | H:#446 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/426-381-15-74-141-lifecycle-real-schema-20261014` | `4104235cda89` | ? | ? | H:#523 B:#533 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/426-a3-01-02-closed-t1-20261015` | `98771904549c` | ? | ? | H:#605,#544 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/74-cas-t1-owner-fkoff-regressions-20261014` | `07745ff124d2` | ? | ? | H:#533 B:#534 | `PRESERVE_HISTORIC_PR_BASE` |
| `evidence/74-cas234-app-regression-redfirst-20261015` | `9bfdfe9f5919` | ? | ? | H:#604,#542 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/aic426-ve1-pg17-linux-windows-20261012` | `be2802c2b526` | ? | ? | H:#475 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/r6d-429-windows-collector-20261011` | `c06dfecd96fd` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-composed-r0-fa315f95-20261012` | `1dfed3180c2a` | ? | ? | H:#460 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-g3-2285e2fc-20261012` | `f21a6cb8d6d7` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-g3-dc238b2f-20261012` | `35bb476ae514` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-integrated-pg-335d2a03-20261012` | `8a5045e9fb86` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-integrated-pg-77665dc6-20261012` | `c3fbc49947d1` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-integrated-pg-b929a0e6-20261012` | `3b020dd80418` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-postmerge-pg-af0feb26-20261012` | `d80af9ae706f` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-1b419304-20261011` | `7b954299f68b` | ? | ? | H:#454 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-2285e2fc-20261012` | `766da55cf9bc` | ? | ? | H:#458 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-2285e2fc-targeted-20261012` | `9903aa5b6809` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-39851dfa-20261011` | `cf9fe0186eb2` | ? | ? | H:#453 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-dc238b2f-20261012` | `fba74d6f5be3` | ? | ? | H:#457 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-e431ff68-20261011` | `81a4a1c5c738` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-r0-pr440-388a053b-20261010` | `f1185b1f74b0` | ? | ? | H:#448 | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-06dbba1c-20261012` | `bd853fa3dd9b` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-4e790c1a-20261012` | `cb79de27facf` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-5d4bc4fb-20261012` | `42717cfd4928` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-62eff74e-20261012` | `4dc8a5619a50` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-62eff74e-4cases-20261012` | `0fcf3ae8d37c` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-adb09536-20261012` | `e074c0c58300` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `evidence/sec408-ws-pg-fa315f95-20261012` | `fd54bd7ac869` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `feature/156-aic31b-forward-pg-20261013` | `66c7ec8f9e68` | ? | ? | H:#500 B:#504,#503,#502 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/156-pg-alembic-version-width-20261012` | `f7efcd63c479` | ? | ? | H:#632,#494 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/164-b0-cross-process-native-lifecycle-fence-20261012` | `8d653f07e088` | ? | ? | H:#470 B:#472 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-d2-tree-recovery-fences-20261012` | `41b46411d8f4` | ? | ? | H:#627,#465 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/164-b0-generation-permanent-revoke-20261012` | `fb925cbdf814` | ? | ? | H:#623,#487 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/164-b0-invocation-authority-fence-20261012` | `3e0bf347863f` | ? | ? | H:#477 B:#480,#478 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-invocation-revocation-fence-20261012` | `25a3b97ee08d` | ? | ? | H:#479 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/164-b0-invocation-revocation-sibling-20261012` | `3a8e6f173819` | ? | ? | H:#480 B:#490,#485,#484 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-native-broker-durable-prototype-20261012` | `04fe81a228e4` | ? | ? | H:#463 B:#466,#465 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-native-root-crossprocess-custody-20261012` | `395b37a44bc1` | ? | ? | H:#473 B:#477 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-os-owned-cgroup-verified-empty-20261012` | `b44cd5ba7fae` | ? | ? | H:#468 B:#469 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-recovery-root-barrier-20261012` | `b481760168f0` | ? | ? | H:#466 B:#468 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-supervised-stopped-child-20261012` | `55904e46358e` | ? | ? | H:#472 B:#473 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-verified-empty-manager-audit-20261012` | `353af06d3989` | ? | ? | H:#469 B:#470 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-winjob-atomic-create-20261012` | `26699fe237f7` | ? | ? | H:#486 B:#487 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-b0-winjob-kill-on-close-evidence-20261012` | `077c288e2279` | ? | ? | H:#485 B:#486 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-s0-permanent-generation-tombstone-pr480-20261012` | `3b845e468449` | ? | ? | H:#490 B:#498 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/164-s1a-v2-deny-only-service-registration-20261013` | `ba36bd12bc3d` | ? | ? | H:#498 B:#499 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/359-a1-r8fork-native-receipt-child-20261012` | `cf648c2dd34d` | ? | ? | H:#495 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/371-t12-b0-private-broker-adapter-20261012` | `94a4151b1979` | ? | ? | H:#474 B:#497 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/381-aic31b-external-deny-barrier-s0-20261014` | `e38e13897186` | ? | ? | H:#519 B:#524 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/417-s1a-native-ipc-ci-pr498-20261013` | `2c86f4d2159a` | ? | ? | H:#620,#499 | `PRESERVE_PENDING_ANCESTRY` |
| `feature/426-a3-01-02-closed-ledger-verifier-20261015` | `d317e40036b0` | ? | ? | H:#540 B:#544 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/aic426-ve1-nonwired-20261012` | `6edd86a050cb` | ? | ? | H:#467 B:#496,#494,#492,#491,#489,#483,#481,#476 | `PRESERVE_HISTORIC_PR_BASE` |
| `feature/central-asset-storage-f1` | `1f88641c6161` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `feature/context-memory-personalization-future` | `8a870fc836f1` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `fix/156-426-aic31b-security-p1-audit-20261013` | `bb0d110e7400` | ? | ? | H:#504 B:#506,#505 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/156-aic31b-cli-sqlstate-oracle-20261014` | `5b3d01fc0666` | ? | ? | H:#518 B:#520 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/164-local-offline-agent-deny-onepath-20261014` | `9f7336d879de` | ? | ? | H:#536 B:#564 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/164-sbx3-agent-command-deny-20261012` | `c10703ada7c3` | ? | ? | H:#462 B:#513,#463 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/242-gateway-effect-timeout-20261013` | `222273c010f6` | ? | ? | H:#507 B:#508 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/242-hitl-auth-generation-deny-20261013` | `bd40a0e68e82` | ? | ? | H:#501 B:#507 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/359-logout-false-204-ack-p0-20261014` | `add6e9c671f5` | ? | ? | H:#522 B:#535 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/359-pr522-p0a-session-delete-failclosed-20261014` | `aec33975d40d` | ? | ? | H:#637,#537 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/359-pr522-p0b-confirmed-delete-20261015` | `d3f53d2b5176` | ? | ? | H:#538 B:#545 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/359-remote-timeout-unknown-no-retry-20261014` | `f5274d043675` | ? | ? | H:#510 B:#515,#512 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/359-terminal-remote-error-client-retry-clamp-20261014` | `d46e0ad942ad` | ? | ? | H:#515 B:#516 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/426-aic31a-retired-replace-redfirst-20261015` | `d7b19b9dff47` | ? | ? | H:#606,#539 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/alpha-aic31b-explicit-target-20261018` | `bc4116c8fc64` | ? | ? | H:#642 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/alpha-aic5b-pg17-width-20261018` | `29e54e34181d` | 4 | 97 | H:#639 B:#640 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/alpha-sbx3-mcp-wire-cert-20261018` | `12f17aa444c1` | ? | ? | H:#643 B:#644 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/alpha-sbx3-mcp-wire-parity-20261018` | `bcc046554402` | ? | ? | H:#644 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/alpha-terminal-permission-two-head-oracle-20261018` | `06ad6a8658f4` | ? | ? | H:#646 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/direct-terminal-auth-547-t2-20261016` | `615ee9482d50` | ? | ? | H:#613,#555 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-417-ci-oracle-reconnect-20261016` | `58f3490f47a9` | ? | ? | H:#581 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ingress-junit-contract-20261016` | `8f847ee754f0` | ? | ? | H:#609,#583 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-client-cas-20261016` | `76249e75323b` | ? | ? | H:#565 B:#566 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-client-cas-r2-20261016` | `733e89a2713e` | ? | ? | H:#570 B:#571 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-client-cas-r3-20261016` | `733e89a2713e` | ? | ? | H:#573 B:#577,#574 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-mcp-20261016` | `32fc68e57711` | ? | ? | H:#566 B:#567 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-mcp-r2-20261016` | `32fc68e57711` | ? | ? | H:#571 B:#572 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-mcp-r3-20261016` | `6a80a1fd211c` | ? | ? | H:#574 B:#576,#575 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-mcp-r4-20261016` | `6a80a1fd211c` | ? | ? | H:#577 B:#583,#581 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sbx3-ta-r3-p0-revoke-20261016` | `66b6125de417` | ? | ? | H:#567 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-revoke-r2-20261016` | `ab36aa52aa6d` | ? | ? | H:#572 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-revoke-r3-20261016` | `88be1e5ad88a` | ? | ? | H:#575 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-revoke-r4-20261016` | `88be1e5ad88a` | ? | ? | H:#635,#576 | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-revoke-r5-20261016` | `6a80a1fd211c` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `fix/sbx3-ta-r3-p0-send-20261016` | `14bcbe77d4f4` | ? | ? | H:#564 B:#573,#570,#565 | `PRESERVE_HISTORIC_PR_BASE` |
| `fix/sec408-ws-sql-namespace-2285e2fc` | `fa315f955731` | ? | ? | H:#459 B:#460 | `PRESERVE_HISTORIC_PR_BASE` |
| `integration/ae359-main-20261016-draft-r1` | `f27205a37b19` | ? | ? | H:#607,#580 | `PRESERVE_PENDING_ANCESTRY` |
| `issue/161-crt1-target-routing-final` | `e539350d6bfa` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/161-crt1-target-routing-impl` | `83b72f346160` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/161-crt1-target-routing-impl2` | `f03df26e8629` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/161-crt1-target-routing-impl3` | `4bc1cd314c5c` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/321-tbo2-production-reanchor-2fb68abb` | `c6aceccc154e` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/321-tbo2-production-reanchor-7f850d69` | `b726fc8737f7` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `issue/359-a1-native-durable-receipt-failclosed-20261012` | `30a82a14d7b4` | ? | ? | H:#617,#488 B:#495 | `PRESERVE_HISTORIC_PR_BASE` |
| `issue/359-r14e-p1a-hard-restart-e2e-20261012` | `2fccc0d5f5ab` | ? | ? | H:#619,#464 | `PRESERVE_PENDING_ANCESTRY` |
| `issue/359-r14e-p1b-durable-pending-gate-20261012` | `898436f8d38e` | ? | ? | H:#618,#482 | `PRESERVE_PENDING_ANCESTRY` |
| `issue/408-security-skill-publication-p1a-v31` | `2285e2fcb433` | ? | ? | H:#440 B:#459,#458,#457,#455,#454,#453,#452,#451,#449,#448,#446,#442 | `PRESERVE_HISTORIC_PR_BASE` |
| `issue/416-cas-f6-win-node-d0-diagnostics` | `1fbcbc48087a` | ? | ? | H:#602,#435 | `PRESERVE_PENDING_ANCESTRY` |
| `issue/426-aic-vE1-foundation-20261012` | `f8ab733b6fb8` | ? | ? | H:#471 B:#475 | `PRESERVE_HISTORIC_PR_BASE` |
| `issue/430-sec-r0-cross-principal-d0` | `bbdc405f9137` | ? | ? | H:#431 | `PRESERVE_PENDING_ANCESTRY` |
| `main` | `858004fae177` | ? | ? | — B:#597,#596,#595,#594,#593,#582,#580,#562,#559,#556,#554,#553,#552,#550,#547,#543,#542,#540,#538,#522,#521,#519,#517,#510,#501,#488,#482,#474,#471,#467,#464,#462,#461,#444,#443,#441,#440,#439,#436,#435,#432,#431,#429,#428,#420,#418,#415,#414,#413,#411,#404,#403,#401,#398,#394,#393,#391,#388,#386,#383,#378,#375,#373,#372,#368,#366,#364,#363,#360,#357,#353,#352,#351,#349,#346,#344,#343,#342,#339,#337,#336,#335,#332,#328,#327,#326,#324,#323,#319,#318,#316,#312,#311,#308,#306,#305,#303,#300,#299,#297,#296,#294,#293,#292,#286,#284,#281,#280,#277,#272,#271,#269,#268,#267,#264,#263,#259,#258,#256,#255,#253,#251,#250,#247,#246,#244,#243,#241,#240,#238,#237,#235,#233,#229,#227,#224,#223,#220,#219,#217,#215,#213,#212,#210,#208,#207,#204,#203,#202,#201,#199,#198,#196,#195,#193,#192,#190,#189,#188,#187,#186,#183,#180,#179,#178,#175,#173,#172,#171,#170,#157,#155,#154,#153,#140,#138,#136,#132,#131,#130,#127,#125,#124,#121,#120,#118,#117,#114,#112,#111,#110,#108,#106,#105,#104,#103,#101,#100,#98,#97,#96,#95,#94,#93,#92,#91,#89,#88,#87,#86,#84,#83,#82,#81,#80,#79,#78,#77,#76,#75,#73,#72,#71,#70,#69,#67,#66,#65,#64,#63,#62,#61,#60,#59,#58,#57,#56,#55,#51,#50,#48,#46,#45,#44,#43,#40,#39,#37,#35,#33,#32,#29,#28,#24,#23,#22,#21,#20,#19,#18,#17,#13,#11,#9,#1 | `PRESERVE_CORE` |
| `plan/371-164-359-native-ae-receipt-b0-20261012` | `0e40439228f8` | ? | ? | H:#624,#484 | `PRESERVE_PENDING_ANCESTRY` |
| `reanchor/ae-r13-b-bd64b675` | `f146f115721a` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `reanchor/cl-ui-2a-d967165a` | `98d8cb98fc03` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `security-p1b-d2-masked-20261011` | `69e7c9a2ce5b` | ? | ? | H:#452 | `PRESERVE_PENDING_ANCESTRY` |
| `security-p1b-d2-spawn-20261011` | `5d3abf0085ee` | ? | ? | H:#451 | `PRESERVE_PENDING_ANCESTRY` |
| `security-p1b-evidence-20261010` | `e3a912579a6c` | ? | ? | H:#449 | `PRESERVE_PENDING_ANCESTRY` |
| `stable/ae359-two-os-after595-20261018` | `e7fb2d0eccf4` | ? | ? | H:#598,#597 | `PRESERVE_CORE` |
| `test/164-agent-process-deny-two-os-20261014` | `9e86091b5c9b` | ? | ? | H:#513 B:#536,#525 | `PRESERVE_HISTORIC_PR_BASE` |
| `test/164-direct-t1-trusted-positive-20261016` | `aadd5a419390` | ? | ? | H:#636,#547 B:#555 | `PRESERVE_HISTORIC_PR_BASE` |
| `test/242-gateway-deadline-contract-negatives-20261013` | `6b10e820e205` | ? | ? | H:#610,#508 | `PRESERVE_PENDING_ANCESTRY` |
| `test/359-pr522-redis-logout-redfirst-20261014` | `613f5d70ddf8` | ? | ? | H:#535 B:#537 | `PRESERVE_HISTORIC_PR_BASE` |
| `test/359-pr538-security-t1-redfirst-20261015` | `77dec33dd447` | ? | ? | H:#545 B:#546 | `PRESERVE_HISTORIC_PR_BASE` |
| `test/359-pr546-otp-atomic-oracle-child-20261016` | `2209029f7c89` | ? | ? | H:#548 B:#549 | `PRESERVE_HISTORIC_PR_BASE` |
| `test/359-remote-unknown-noretry-two-os-20261014` | `aaa2b7a06e76` | ? | ? | H:#628,#512 | `PRESERVE_PENDING_ANCESTRY` |
| `test/359-terminal-error-clamp-two-os-20261014` | `b874d3e02f8f` | ? | ? | H:#516 | `PRESERVE_PENDING_ANCESTRY` |
| `test/371-t1-prepositive-negative-20261013` | `2f0088f1f6a6` | ? | ? | H:#621,#497 | `PRESERVE_PENDING_ANCESTRY` |
| `test/a12-t1-runtime-security-redfirst-20261016` | `79768275f76e` | ? | ? | H:#550 | `PRESERVE_PENDING_ANCESTRY` |
| `test/ae359-postack-retry-crossstore-t1-20261016` | `cdf709c78ab3` | ? | ? | H:#612,#579 | `PRESERVE_PENDING_ANCESTRY` |
| `test/alpha-aic-pg17-preflight-oracles-20261018` | `b77c6f9af3f0` | ? | ? | H:#640 | `PRESERVE_PENDING_ANCESTRY` |
| `test/alpha-sbx3-mcp-wire-cert-redfirst-20261018` | `0c4bb9b60931` | ? | ? | H:#641 B:#643 | `PRESERVE_HISTORIC_PR_BASE` |
| `tmp/231-gac2-close-drain-c6b312` | `ea144620b575` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `tmp/231-gac2-equivalent-reanchor-ab6718c` | `009ac2a68e99` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `tmp/231-gac2-refresh-854ae02d` | `9a203d705bdc` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `tmp/231-gac2-stack-46660ef9` | `6d7252cf603c` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `work/ae-r11-boundary-7569e9ea` | `74ef82544557` | ? | ? | H:#32 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ae-r12-f3-preclaim-contract-6acf6c5f` | `743f2095e9e2` | ? | ? | H:#199 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ae-r12-f3b-preclaim-contract-ea6c5f00` | `a2313faac52f` | ? | ? | H:#208 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ae-r12-h0-full-fault-exit-854ae02d` | `dfc0e1d84f7b` | ? | ? | H:#241 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ae-r13-b-telemetry-migration-c5675f14` | `996fbe76ac3f` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `work/cas-f5-d-p1-provider-attempt-projection` | `9a4e540ef21e` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `work/cl-ui-r6-d-win-d0-diagnostic-604a1aba` | `3e339eb5e077` | ? | ? | H:#601,#429 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ctx-f1-refresh-3a01b4ca` | `e93dd8c25c38` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `work/ctx-f2-source-identity-af0c90c0` | `543448a11e1b` | ? | ? | — | `PRESERVE_PENDING_ANCESTRY` |
| `work/ctx-f5-3i-b0-e27e2a04` | `e9b82c940a8d` | ? | ? | H:#193,#192 | `PRESERVE_PENDING_ANCESTRY` |
| `work/r9-h-d0-sqlite-adopt-scan-diagnostic` | `5c75bcad7230` | ? | ? | H:#411 | `PRESERVE_PENDING_ANCESTRY` |
| `work/ubq-5b-provider-timeout-7bfad646` | `e1ee7ff86b5e` | ? | ? | H:#217 | `PRESERVE_PENDING_ANCESTRY` |

## Additional custody
- PR #667/S02 and PR #668/S03 source branches no longer exist in the remote branch listing; no deletion performed here.
- PR #666/S01 is closed without merge and its unique evidence commit remains separate from alpha, so preserve that branch.
- A full descendant-branch ancestry matrix across all pending refs has **not** been proven in this snapshot. **Do not delete** pending refs without that proof.
- #655 retains full post-merge CI evidence; #656 maintains current architecture docs; #657 coordinates stage handoff; #661 S04 stays HOLD pending Owner's audit acceptance.
