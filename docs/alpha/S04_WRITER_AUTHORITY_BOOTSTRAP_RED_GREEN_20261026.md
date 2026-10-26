# S04 writer authority — bounded RED-first repair (2026-10-26)

Scope: #661, based on Draft PR #671 at `b20a925b8dae419d886c6f7c017c6c040df5d2ab`.
Non-production. **Do not merge, release, mark Ready, or change protected negative tests.**

## RED-first

The unchanged real Redis 7.4 / PostgreSQL 17 mixed suite in PR #671 still
reports **six S04_MIXED_SEMANTIC_RED**: for registration and password_reset,
(1) legacy Redis + modern SQL-only, (2) old unconditional Redis SET after
modern acknowledged NX, and (3) legacy Redis + modern InMemory. Four isolated
backend controls PASS. Exact prior run:
https://github.com/boxs-51/assistant/actions/runs/39893780222

A newly added unit oracle reproduces **S04_BOOTSTRAP_SEMANTIC_RED** on the
unmodified bootstrap because it calls `get_cache_driver()` for OTP, selecting
the general default InMemory cache. It was run first and failed 1/1; the
test-only local checkpoint is `0fd7aa8` (local-only provenance, not CI).

## First bounded fix: bootstrap backend selection

`select_otp_cache_driver(storage_engine, auth_enabled=...)` now separates
OTP backend choice from generic rate-limiting cache selection. When
authentication is enabled, a registered and available Redis backend must be
resolved by the explicit name `redis`; missing/unavailable/invalid drivers
raise `OTPBackendUnavailable`, never falling back to independent SQL or
per-process memory authority. Auth-disabled development bootstrap preserves
the pre-existing cache default. Unit probes cover each failure and positive
branch; neither protected physical suite nor the isolated SQL/InMemory
positive controls are altered.

## Security boundary NOT YET fixed

**This fixes one independently observed bootstrap downgrade, not all six
physical mixed-version REDs.** A previously deployed binary with Redis SET
privilege can still overwrite a modern NX challenge irrespective of which
backend the modern process selects. No Python-only patch can revoke an
existing Redis ACL credential, a stale promoted replica, or a pre-opened
SQLite file handle. A trusted Owner-controlled **external** rollout
coordinator must (a) enumerate actual old binaries, identities and backend
topology, (b) stop/drain old processes and handles, (c) revoke distinct old
Redis writer privileges on *every* failover candidate, (d) attest immutable
writer epoch/backend choice per principal/purpose, and (e) deny admission on
missing evidence, unknown ACK, rollback or shared/unfenced privileges.

A config/env boolean, a new application's self-attestation, or an
unverified Redis health check is **not** a trusted writer-epoch grant.
An authentic old-binary + new-binary disposable physical suite, payload
persistence, multi-Sentinel and rollback live-OTP tests are still required.

Gate: **BOOTSTRAP_BACKEND=LOCAL_UNIT_GREEN**;
**S04_MIXED_AUTHORITY=BLOCKED_P1, NO_READY/NO_MERGE/NO_DEPLOY**.
