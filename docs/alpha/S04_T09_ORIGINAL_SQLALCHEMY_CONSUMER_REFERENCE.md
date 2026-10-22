# S04 T09 — original historical SQLAlchemy OTP consumer, no production edit

At pinned original commit `40b502cbb1ae7d678e536e68b4359322bab467ca`,
load verbatim three blobs: OTP service `5a7e6bcdba3d5eec5fcf34a7e8760508a4401271`,
PendingRegistrationRepository `59a7b24560782b3f1336cfea0bfb7f4f67010112`,
and mapped PendingRegistration model `c2a687774f0d82d15551ede43001895d321eef75`.
The historical consumer invokes the actual original repository
`session.get() -> session.delete() -> session.flush()` inside
SQLAlchemy AsyncSession and explicit COMMIT; Redis uses real 7.4 ACL/GET/DEL.
The standalone UOW, SQLAlchemy engine, import shims and disposable fixtures
are NOT the historical shipped service binary, dependency injection system,
production migrations, or runtime config.

### Exact physical matrix (registration and password_reset per row; 16 cases)

| Mode | Expected result |
|---|---|
| PG Redis positive | Old OTP can redeem Redis; SQL row remains (risk if dual-backed) |
| PG -GET + SQL DELETE allowed | Original SQLAlchemy repository redeems fallback |
| PG -DEL + SQL DELETE allowed | Original SQLAlchemy repository redeems fallback, Redis unchanged |
| PG -GET + SQL DELETE revoked | SQLAlchemy DELETE denied, row and Redis retained, new old process denied |
| PG -DEL + SQL DELETE revoked | SQLAlchemy DELETE denied, row and Redis retained, new old process denied |
| PG Redis enabled + SQL DELETE revoked | Legacy can still redeem Redis (independent unsafe authority) |
| SQLite -GET, no write guard | Original SQLite SQLAlchemy DELETE consumes fallback |
| SQLite -GET, BEFORE DELETE denial trigger | Original SQLite SQLAlchemy DELETE fails closed; row retained across new old process |

**RED-first evidence:** T08 independently established that the historic OTP
consumer falls back to SQL after Redis -GET/-DEL. T09 removes the T08
`DELETE RETURNING` adapter gap by executing original historical repository
and mapped model with SQLAlchemy physical backends. The unsafe controls are
expected hazard reproduction, not application GREEN.

**Scope caveat:** SQLite triggers are test-only and can be bypassed by actors
with file/schema powers; SQLite lacks PG roles. This only demonstrates statement
behavior, not durable OS access confinement, backups, rollback, crash or restart
equivalence. PostgreSQL role revoke is a disposable test identity, not an
externally attested old binary identity. No replica/Sentinel promotion, PG WAL
failover, production account mutation or legacy service startup is tested.

S04 #661 remains **SECURITY_HOLD** until original deployed binary and real
identity/role/authentication privileges are reliably fenced for issuance and
redemption including DB and Redis after cutover, together with full v2 endpoint
and owner parity/security gates. Six mixed-version semantic RED must remain
unchanged; no merge/deploy or Codex Review.
