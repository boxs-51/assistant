# S04 T08 — PINNED historical consumer Redis GET/DEL + SQL fallback authority

**Issue:** #661 · **PR:** #671 · **Scope:** T08 test-only (no production changes).
The original OTPStorageService methods from Git blob
`5a7e6bcdba3d5eec5fcf34a7e8760508a4401271` at
`40b502cbb1ae7d678e536e68b4359322bab467ca` execute verbatim.
The Redis cache and SQL pending-repository/UOW are physical test-only adapters
calling real Redis 7.4 commands and PostgreSQL 17 DELETE RETURNING/COMMIT.
This is **not a complete old binary** or verified production ORM equivalence.

**RED-first symptom:** historic Redis `GET` or `DEL` exceptions are
swallowed in `_consume_cache_value`, then `_consume_database_value`
attempts SQL `get_and_delete`; therefore Redis ACL alone cannot eliminate
legacy OTP *redemption* authority when SQL DELETE remains allowed.
The deliberately unsafe controls are **EXPECTED_HAZARD_REPRODUCTION**, not
production GREEN. The dual-fence controls test a trial mitigation.

| ID (registration / password_reset) | Required result |
|---|---|
| T08-01 | Healthy Redis GET + DEL gives one old redemption, removes Redis key; replay denied |
| T08-02 | Redis -GET while SQL DELETE allowed => historical SQL fallback redeems |
| T08-03 | Redis -DEL while SQL DELETE allowed => historical SQL fallback redeems, Redis key persists |
| T08-04 | Redis -GET AND SQL DELETE revoked => fail closed, SQL row and Redis key retained, new old process denied |
| T08-05 | Redis -DEL AND SQL DELETE revoked => fail closed, SQL row and Redis key retained, new old process denied |

Exact historical SHA + Git blob checked before execution; the old service's
`verify_and_get_data`, `verify_password_reset`, `_consume_cache_value`
and `_consume_database_value` remain unedited. Parent process checks actual
Redis key and PostgreSQL row states after every child execution.

**Safety/limits:** Disposable synthetic user and OTP; PG admin and Redis
default user are fixture-only, network localhost CI. Child credentials are
provided over stdin and never printed in error paths. The test's PostgreSQL
table/repository adapter and auth API are NOT faithful production deployments,
and it has not tested real old binary/SQLite, Redis replication/ACL SAVE,
failover or interruption, in-flight old DB session, credential role inheritance,
new v2 account effects, controller attestation, expiry, or physical tokens.
No main merge/deploy, no production source or migration changes, no changes to
the original six mixed-version SEMANTIC_RED/four positive controls.

**Release gate:** This reference can PASS and issue #661/S04 still
remains BLOCKED until original binary, all writer + consumer paths,
persistent SQLite/PG and Redis authorities are externally fenced and proven,
owner data parity is verified, and v2 endpoint/HA proofs are complete.
