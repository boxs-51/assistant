# S04 T06 — physical PostgreSQL 17 role and epoch boundary (REFERENCE ONLY)

**Origin:** issue #661, PR #671. **Scope:** new test-only Python file, one isolated
PostgreSQL 17 workflow, this document. No production/migration edits, no merge,
no deployment, no independent Codex Review.

## Frozen objective and gate

Test whether SQL privileges plus a durable authority epoch can reject stale
writer activity in a *disposable PG17 database*. Preserve six legacy Redis/SQL/
InMemory mixed-authority SEMANTIC_RED and four positive controls unchanged.
A successful T06 is **NOT** evidence that a real old binary using Redis has
lost issuance or redemption authority.

The role names are random, test-local, unprivileged PostgreSQL LOGIN roles
with trust authentication only on an ephemeral GH Actions service.
The simulated controller is the disposable bootstrap superuser, **not** an
external trusted admission service or a production RBAC design. T06 does not
authenticate callers, distribute credentials, test pg_hba.conf/SSL, failover,
Redis ACL, an old binary, issuance delivery, rollback after first v2 OTP ACK,
or a real account migration. The SQL function uses a fixed search_path
and fully qualified objects. Tests do not create persistent application tables.

## Required physical matrix

| ID | Positive/negative oracle |
|---|---|
| T06-01 | Old SQL writer can write pre-fence, then cannot write after REVOKE |
| T06-02 | SEALED and old epoch denied; ACTIVE correct epoch ACK; FROZEN denied |
| T06-03 | Old cannot call SECURITY DEFINER; modern cannot bypass via direct DML |
| T06-04 | Existing old SQL session and reconnect both denied after cutover |
| T06-05 | Monotonic epoch and terminal FROZEN reject rollback |
| T06-06 | Modern caller cannot hijack SECURITY DEFINER via untrusted search_path |
| T06-07 | Duplicate issuance ID raises unique violation, not a false ACK |
| T06-08 | An in-flight issuer holds authority FOR SHARE; controller freeze waits |
| T06-09 | Old SQL role cannot self-GRANT or SET ROLE to controller |

All nine require PG17 physical, expected negative controls, exact-head JUnit
identity and 0 skip/error/failure. An unexpected exception is not proof of a
security denial. This matrix does NOT prove old in-flight SQL transactions have
drained, and does NOT fence Redis/SQLite/InMemory writers.

## Next mandatory implementation/release gates

External trusted verifier/controller signature and epoch authenticity;
all deployed binaries and sidecars observed/revoked; legacy Redis ACL/Sentinel
including unsaved ACL/restart/failover; old binary physical attempts; PG17
replication and commit receipt high-water; account authority/write-fence for
SQLite source, owner integrity parity, full consume-and-apply endpoint, session
credential-version fence; disaster recovery after irreversible handoff.
Until then **S04_SECURITY_HOLD** remains mandatory.

**Evidence status:** PENDING_PHYSICAL until this exact-head CI succeeds.
