# S04 pre-fence legacy Redis writer: RED-first test contract

**Stage:** #661 (AE359) · **Parent:** #650 · **Predecessor:** #660 scoped PASS/MERGED_ALPHA
**Frozen nonproduction base:** `alpha@ee2a71c5cbdce1aed2431fed62f05f7cf79e8ddb`
**State:** RED_OBSERVED on exact test-only HEAD; bounded SET-NX repair submitted, GREEN_NOT_YET_OBSERVED. Never infer production readiness.

## Fault model and why the current fence may not suffice

The current `OTPStorageService._reserve_issuance()` commits a per-purpose hashed
SQL reservation before the modern Redis `SET`. An **older writer** that directly
writes `pending_reg:<email>` or `pending_password_reset:<email>` may have left
a live challenge without the modern SQL reservation. A new writer can then
reserve the *previously absent* fence and perform an unconditional Redis SET,
potentially replacing the still-live legacy challenge.

The exact old wire shape is reconstructed using a separate, genuine Redis
client and real Redis 7.4 and PostgreSQL 17. It is **not** proof of executing
an old-version application binary, OS process, failover or HA.

## Frozen oracle

For each purpose (registration and password_reset):
1. Physical legacy Redis client issues OTP 111111 with TTL 300 and no SQL fence.
2. Independent modern RedisDriver and real PG17 UOW try issuing OTP 222222.
3. The original raw Redis value is byte-for-byte preserved and retains live TTL.
4. The modern issue attempt is **rejected**; no second SQL OTP row appears.
5. Existing OTP authenticates exactly once; modern OTP does not authenticate.

A negative control that the first challenge was not actually live is
`HARNESS_ERROR`, not semantic RED. No mocks, `xfail`, skipped security
assertions, manufactured PASS/unknown ACK or permission changes. The new test
must first fail on the frozen code before considering a fix.

**Classification:** if a correctly admitted physical backend produces
unconditional overwrite, record `S04_SEMANTIC_RED`; if fixtures/runner fail,
record `HARNESS_ERROR` or `INFRA_FAILURE`. Preserve the raw JUnit and the
exact PR head SHA. Never mark the entire S04 PASS from these two cases.

## Required subsequent work (still HOLD)

- Actual old/new-version binary interoperability and Redis failover/restart,
  TTL expiry/reissue, cross-node attempts, unknown COMMIT and unknown ACK.
- Refresh/logout token linearization, OTP DB atomic consume, HITL one-use
  effect and Windows physical equivalence.
- Verify the original R-ACK-07/08/09 tests are unchanged and remain GREEN if
  a minimal future repair is admitted.
- Review provenance, exact SHA regression, and readiness gate before promotion.
- #655 merged-alpha integrated full-suite evidence is a separate blocker.

**Actions excluded:** no edits to original negative/security oracles, no source
repair before physical RED, no production services, main merge, deploy,
Native enablement or security permissions changes.

## Exact physical RED evidence and bounded repair (2026-10-21)

- Test-only PR #670 HEAD `fadee55e4461da587b7b7bad7e774a539726af00`.
- GitHub Actions [run 39575672438](https://github.com/boxs-51/assistant/actions/runs/39575672438),
  job [119140457555](https://github.com/boxs-51/assistant/actions/jobs/119140457555):
  **2 S04_SEMANTIC_RED / 0 ERROR / 0 SKIP** under real Redis 7.4 +
  PostgreSQL 17.11. Registration and password-reset legacy-first physical
  tests each observed modern writer outcome=SUCCESS with old challenge
  destroyed; raw JUnit artifact ID `12334582437`.
- Minimal source repair: for the two OTP challenge key namespaces only,
  Redis SET is now sent with **NX + EX atomically**, not GET-then-SET.
  Redis-confirmed NX collision produces typed `CacheWriteCollision`
  distinct from `CacheWriteNotDispatched` and uncertain network ACK.
  Other cache/cooldown keys retain ordinary SET semantics.
- Existing R-ACK and security negative tests are not edited. Any final
  GREEN must be witnessed at exact *new* PR head and inspect raw job logs;
  CI success is not a cross-version release certificate.
- This only blocks a **modern write following a live legacy write**. A
  still-unrestricted legacy client can overwrite modern data later. No
  security claim for legacy-after-modern, running old binary, failover or
  migration admission until the old write path is retired/gated and both
  directions independently verified.
