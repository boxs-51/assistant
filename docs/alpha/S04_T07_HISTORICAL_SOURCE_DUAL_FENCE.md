# S04 T07 — genuine historical OTP source, dual Redis + SQL fence

This is an isolated PG17 + Redis 7.4 GH Actions experiment derived from issue #661.
It runs the **unchanged original OTPStorageService methods** from the pinned
2026-09-17 commit `40b502cbb1ae7d678e536e68b4359322bab467ca` (Git blob
`5a7e6bcdba3d5eec5fcf34a7e8760508a4401271`) in a separate
Python subprocess. Only the historical import dependencies are substituted
with lightweight adapters; Redis `SET` and PostgreSQL fallback `INSERT`/
`UPDATE` execute against disposable, real servers.

It is *not* the old packaged application binary, server boot, ORM schema or
production user account. Redis/PG credentials, role, schema and test OTP are
isolated/fake. The deliberate pre-fence and Redis-only controls must
reproduce the historical authority hazard; no security oracle is weakened.

| Matrix | Expected physical observation |
|---|---|
| T07-A registration/reset | Historical source can write Redis pre-fence; SQL untouched |
| T07-B registration/reset | Redis ACL -SET causes historical code to fall back into SQL; unsafe legacy authority witnessed |
| T07-C registration/reset | Redis -SET PLUS revoked SQL INSERT/UPDATE makes the same historical method fail closed, including new process/reconnection; no OTP record written |

**Evidence limits:** These six tests do not prove real old-version binary,
run-all-service startup, Redis Sentinel/ACL SAVE/restart, persistent SQLite
writer revocation, cached passwords/role sharing, fallback via other interfaces,
PG role/grant provenance on promoted primaries, or terminal credential/session
version controls. Six mixed-authority SEMANTIC_RED stay frozen on the same PR
and remain a product S04 P0 BLOCK. A reference PASS does not authorize cutover.

Hard constraints: no production source, migration or security oracle edits;
no merge/deploy/independent Codex Review. Next gate must run a genuine old
application process with its actual dependencies, assert all old ingress writer
paths denied after trusted authority activation and check rollback/failover.
