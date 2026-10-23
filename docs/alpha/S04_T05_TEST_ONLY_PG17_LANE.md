# S04 T05-01..04 — isolated PG17 control lane (test-only)

**Type:** non-production TEST-ONLY reference-harness proposal linked to issue #661 and draft PR #671.
**Scope:** a runner-owned disposable PostgreSQL 17 service; no application OTP V2 code is imported.
**Source pin:** PR #671 at c230a1863d5ad5b0d61f53bf324a9a76f760755f before this test-only addition.

The explicit selector `se/tests/integration/s04_t05_pg17_contract_physical.py` does not start with `test_`, so
the strict physical harness is not discovered by portable suites.
The workflow allows a localhost-only database named `s04_t05_contract`, role `s04_test`,
and `GITHUB_ACTIONS=true` plus two strict admission markers. These markers guard the
test but DO NOT authenticate a real release or authorize an operator to target production.

| ID | Physical harness property | Required positive/negative interpretation |
|---|---|---|
| T05-01 | two PG sessions contend for one operation unique key | 1 COMMITTED, 1 CONFLICT and one effect |
| T05-02 | mismatch fingerprint fails; exact replay accepted | CONFLICT/REPLAY cannot create another effect |
| T05-03 | forced same advisory key for two different IDs | lock contention must not merge identities |
| T05-04 | reversed row lock order gives 40P01; sorted order completes | 40P01 is EXPECTED_NEGATIVE_CONTROL only |

**Evidence classification:** 4/4 PASS -> HARNESS_READY only. Other outcomes -> HARNESS_ERROR
or harness-test failure. None of these imply product S04 PASS/RED/GREEN or close #661.
Six previously proven mixed-version semantic RED remain unchanged on PR #671.
The actual old binary, PostgreSQL authority v2, owner migration parity, and failover
are separate HOLDs. Do not weaken any product oracle, merge, deploy, or trigger independent review.

**Next gate:** review raw exact-HEAD JUnit and confirm actual PG17 version before
binding this harness to an independently reviewed V2 adapter. The adapter needs
separate end-to-end registration/password-reset assertions and owner/security parity.

**Negative-control audit:** expected 40P01 is contained inside T05-04; JUnit must
report four passing test-only cases, not application defects. An arbitrary DB outage,
missing artifact, skipped test, absent strict marker, wrong HEAD, non-PG17 server, or
non-disposable database MUST fail closed and must not be labeled semantic RED.
