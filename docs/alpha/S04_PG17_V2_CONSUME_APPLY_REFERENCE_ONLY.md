# S04 PG17 V2 consume-and-apply — non-production reference slice

**Revision:** Candidate reference v0.1; PR #671 child, issue #661. **No release authority**.

Goal: prove on disposable physical PostgreSQL 17 that OTP state, account effect and
operation/effect receipt *can* commit atomically in one transaction, while global
operation-id guard, authority epoch, and row locks serialize concurrent callers.
This file does not implement seven-table ADR v2 or any real product route.

**Isolation:** Only `se/tests/integration/s04_v2_consume_apply_reference_physical.py`
is executed explicitly from its separate PG17 CI lane; not auto-collected in portable
pytest. Uses fake account identifiers and fake credential strings, fixed TEST-ONLY HMAC
key, test-owned randomized PostgreSQL schema, strict localhost/role/db guard, and
`DROP SCHEMA` cleanup. No production identities or account migration.
Exact HEAD and all ten JUnit IDs required, no skip, xfail or unexpected GREEN.

**Transaction:** active PG epoch locked FOR SHARE -> advisory global op serial guard
(full UUID/text PK remains decisive) -> slot FOR UPDATE -> challenge FOR UPDATE ->
account FOR UPDATE -> PG account INSERT/UPDATE -> challenge CONSUMED -> effect
UNIQUE(challenge) and operation receipts -> same COMMIT. Wrong OTP attempt debit
commits in DENIED. Failure after account update rolls back all. DB clock after lock
is the TTL authority. Simulated ACK loss is dropped client *response*, not PG network
ACK-loss or WAL failover proof.

**Remaining HOLD (explicit):** Actual hardened PG authority role/GRANT/REVOKE,
writer epoch external attestation, real old binary, Redis ACL restart/HA, DB
replication and old-primary fencing, SQLite→PG owner integrity parity, full
seven-table migration, OTP delivery/outbox, S04 registration/reset endpoint
integration and token issuance/revocation, password credential-hash contract,
refresh/logout, physical ACK loss. Six mixed-version semantic REDs + four
positive controls on `test_alpha_s04_legacy_redis_otp_writer_redfirst.py`
MUST stay unchanged. A green reference run never closes issue #661.

**Security caveat:** The self-contained function is demonstration-only,
uses a fixture HMAC key and synthetic credentials, and has not undergone an
independent security review. It MUST NOT be imported from or copied into
production services without a separately approved contract and gates.
