# S04 T10 — physical SQLite file/schema authority and old connection

**Finding source:** T09 PG17/SQLite original SQLAlchemy repository 16/16 PASS at
HEAD `5d4f99ded12569c2395b2c6ad2691756738895af`;
T09 only proved the SQLite `BEFORE DELETE` guard while the database schema
and file were not modifiable by the old writer. T10 evaluates this limitation
using *synthetic and disposable* SQLite files on two GitHub-hosted operating
systems. It does not run the packaged historical binary or modify production.

| Case (registration and password_reset) | Expected evidence |
|---|---|
| T10-01 | Already-connected but idle old SQLite connection observes the committed trigger and its DELETE is denied |
| T10-02 | Old BEGIN IMMEDIATE write transaction prevents controller CREATE TRIGGER (SQLITE_BUSY). After draining, trigger install succeeds and DELETE is denied |
| T10-03 | Negative control: if old writer still has DDL/file-write authority, it can DROP the trigger, then DELETE succeeds |
| T10-04 | Negative control: if old writer can replace the database with a pre-fence file snapshot after connections close, the trigger vanishes and DELETE succeeds |

Those last two are **expected hazardous behavior** and must PASS as
reproductions, not be misrepresented as fixed product behavior.
T10-02 requires an orchestrated quiescence gate before cutover.
T10-03/04 demonstrate why a SQLite trigger is NOT an independent security
boundary against a process retaining file/schema permissions.

**Protect:** all operations are scoped to `tempfile.TemporaryDirectory`
with fake OTP/email, and cleanup closes every connection. The optional
`GITHUB_ACTIONS`+strict markers guard accidental execution but are not
security credentials or OS confinement. JUnit must contain 8 identities
on each OS, zero skip/error/failure. A local dry run with admission env
settings is a smoke test, NOT GitHub physical evidence.

**Remaining S04 HOLD:** immutable old process identity, Windows NTFS ACL
and Linux POSIX ACL/namespace/permissions, process handles, immutable
source SQLite files, dual active storage, real old binary, external
trusted epoch fence, Redis restart/failover, PG replication, full account
migration and historical owner semantics. The original 6 mixed-authority
security RED scenarios are unchanged; no source rewrite, migration edit,
merge, deployment, or independent Codex review.
