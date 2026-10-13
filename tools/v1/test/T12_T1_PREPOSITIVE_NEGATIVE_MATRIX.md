# Tools #371 — T1 pre-positive negative-regression contract

Status: **DESIGN + SYNTHETIC NEGATIVE TESTS ONLY**. Owner scope: Tools V1 private terminal.run bridge. No Agent activation, no native positive receipt, no root cleanup authorization, no merge.

## Exact source and ownership anchors (2026-10-13)

- Canonical main: `af0feb268b59cc9828c536eda1629c3a64c39bed`.
- Tools parent: Draft PR #474 HEAD `94a4151b19796bfe287eb6dfbd96adc1551ff464`, private production file `tools/v1/terminal_tool.py` blob `ce593df83b76ec4378c693591d06ff446ce13bd1` (untouched by T1 test-only child).
- SE S0: Draft PR #490 HEAD `3b845e4684495b8d62de3fe6503b1836b438bc13` permanent generation tombstone; negative-only, diagnostic SQLite, no privileged issuer.
- SE Windows Job correction: Draft PR #486 HEAD `26699fe237f7d9b4b53a488a93caa3881f932e20` is a **different sibling**. Job containment does not prove LPAC/network NONE, and PR #490 does not inherit #486.
- SE/Tools ABI #478 HEAD `fb537f4fe1e66c4adf227ac56e4baded292165d0` pins earlier SE #477 + Tools #474; evidence cannot be treated as a combined #490/#486 result.
- #164 proposed S1-A seven SE-owned ADD alternative remains **conditional**; #156 trusted execution-identity join and #417 real distinct-OS-principal CI remain **HOLD**. Administrative policy suspension does not remove technical safety requirements.

## T1-negative gates runnable on the current private Tools bridge

Run alongside `test_t12_b0_private_broker_adapter.py` on actual Linux and Windows runners; all selected cases must execute with zero targeted skip/xfail or exception waiver. The new module tests only synthetic adversarial ports and explicitly patches host `subprocess.Popen` to detect fallback.

| Gate | Adversarial input | Required Tools observation |
| --- | --- | --- |
| T1-N01 | Broker IPC disconnect, broken pipe, deadline, foreign service, restarted epoch | `TERMINAL_OUTCOME_UNKNOWN`, `retryable=false`, `replay_safe=false`; zero host Popen |
| T1-N02 | Forged dict/tuple/bool/string/object pretending to be broker completion | Reject before receipt lookup; never publish success |
| T1-N03 | `None`, false, numeric/string/dict/truthy verifier replies | Identity check rejects; no bool coercion; UNKNOWN nonreplayable |
| T1-N04 | Bad exit code/byte type, observed byte counters, duration type or range | Reject rather than constructing Tool SUCCESS |
| T1-N05 | `OUTCOME_UNKNOWN`, `REVOKED`, `DENY`, fake SQL `COMMITTED`, malformed outcome with receipt-shaped fields | UNKNOWN/nonreplayable even if a *synthetic* verifier claims true |
| T1-N06 | Receipt readthrough disconnect with privileged diagnostic path in error | Nonreplayable UNKNOWN and redact internal credential/path |
| T1-N07 | Missing private port or missing authority | `SANDBOX_PRESPAWN_DENIED` before any dispatch or host Popen |

These tests do **not** verify a real broker even when a test double supplies `verification=True` to exercise malformed completion handling. Existing B0 tests already cover token replay, generation revoke, cancellation, `os._exit` diagnostic ledger and public API isolation; this child does not alter their behavior.

## RED-first tests REQUIRED for a future positive T1 adapter (NOT executable / NOT PASSED here)

| Gate | Owner/evidence source | Required non-bypass behavior |
| --- | --- | --- |
| T1-P01 — principal provenance | SE #164 + CI #417 | A real broker-owned Linux separate UID/GID with SO_PEERCRED, and real Windows SCM service identity, restricted pipe ACL + impersonation token; mocks/same UID cannot pass OS-positive transport gate |
| T1-P02 — trusted entitlement | CRT #156 + SE #164 | Protected server-to-broker immutable binding: owner/execution/sandbox/canonical generation/invocation/toolcall/profile + issuer epoch/revision/expiry, live revocation CAS; never derive from caller JSON/PID, `owner_instance_id` or request context alone |
| T1-P03 — generation grammar | SE #164 / CRT #156 | Reject integer/string alias (`1` vs `01`), noncanonical, stale/future generation at registration, mint, revoke, receipt lookup; cross-process revoke/mint serialized |
| T1-P04 — exactly-once effects | SE #164 / Tools #371 | Two-process replay, crash at reservation, post-dispatch disconnect, stop/cancel all become UNKNOWN, no re-dispatch, no host Popen fallback |
| T1-P05 — native confinement | SE #164 + CI #417 | Genuine Linux user/mount/pid/net+delegated cgroup2 pre-first-instruction and Windows restricted token/LPAC + atomically assigned Job; FS/network NONE including loopback IPv4/IPv6, no skip |
| T1-P06 — durable OS tree and cleanup | SE #164 + CI #417 | OS-owned tree reattach through hard broker/service crash, fork-before/after-revoke, epoch-specific VERIFIED_EMPTY with manager root barrier; never infer empty tree from token, Python bool or `IsProcessInJob` alone |
| T1-P07 — effect receipt vs result visibility | AE #359 + CRT #156 + SE #164 | Broker-authenticated and durable invocation/OS tree readthrough **before** AE SQL COMMITTED or CRT COMPLETED, and on every resumed/fork/RETRY/by-ID/historical read; outage/ambiguous/revoked => DENY/UNKNOWN |
| T1-P08 — revoke/commit linearization | AE #359 / SE #164 / CRT #156 | One documented serializable ordering/fence across broker generation revoke, SE ledger, AE SQLite/PostgreSQL17 result and CRT; a one-time GET_RECEIPT followed by SQL COMMITTED is TOCTOU and must not pass |
| T1-P09 — supported host variants | CI #417 | Verify actual Linux/Windows identities, service provisioning, kernel evidence, JUnit node inventory and exact compositional source pins; absent privilege or zero-selected native test must HARD RED, not green from synthetic tests |

**RED-first** means the future positive-capable implementation must first demonstrate that these test cases detect unsafe admission. It does **not** mean enabling native spawn now, committing deliberately failing OS-positive CI to B0, or treating a synthetic verifier as an issuer.

## Strict implementation sequence and boundaries

1. Accept this test-only Tools evidence as negative behavior only. Ensure two-OS strict workflow and Architecture regression finish on exact HEAD; do not transfer result to a sibling SHA.
2. SE #164 freeze actual S1-A service host/registration physical pathset (v1 five or v2 seven plus any necessary provisioning); #156 provide concrete trusted CAS-backed join or `HOLD_EXTRA_PATH`; #417 demonstrate real distinct-principal runner and later S1-B/C kernel nodes.
3. Independently compose S0 PR #490 with corrected Windows Job PR #486; do not treat old pinned PR #478 as composed proof. Run OS-negative and genuine OS-positive test matrices against one exact candidate.
4. Only after S1-A/B/C and AE/CRT fencing are technically proven, design separate Tools T1 positive adapter branch and explicit fixture for real broker-issued receipts. Keep native model route DENIED until all releases are evidenced.
5. Keep `terminal.launch` detached and `python.run` blocked for model origin; preserve legacy DIRECT semantics and frozen Tools logical ID/metadata. Do not mutate #164/#156/#417 files from this Tools test-only child.

**Current disposition:** `T1_NEGATIVE_SYNTHETIC_ONLY`; `T1_POSITIVE=HOLD`; `AGENT_NATIVE=DENY`; `VERIFIED_EMPTY.production_usable=False`; `AE_NATIVE_COMMITTED=HOLD`; `CRT_NATIVE_COMPLETED=HOLD`; `ROOT_RELEASE=QUARANTINE`; `NO_MERGE`.
