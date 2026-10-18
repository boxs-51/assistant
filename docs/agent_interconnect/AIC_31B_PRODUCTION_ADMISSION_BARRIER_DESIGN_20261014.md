# AIC 31b — Production admission barrier implementation design (issue #381)

**Status:** DESIGN ONLY / NOT DEPLOYED / NOT OPERATOR-SIGNED / SOURCE FINAL HOLD / NO MERGE / AGENT NATIVE DENY.  
**Source pin:** main@`af0feb268b59cc9828c536eda1629c3a64c39bed`; composed AIC PR467→496→500→504→506→509→511; PR514 runbook@`d67604609d7491cb8237e62a09e4e084aaf695f6`. **This branch is docs-only from main, does not contain 31a/31b and cannot be run as a migration.**  
**Ownership:** #381 deployment/migration; #156 runtime & capability; #426 AIC identity/erasure; independent production DBA and security reviewer required. Policy #85 is owner-suspended; security defects and GitHub-enforced protection still apply.

## 1. Verified repository evidence vs unknown production facts

- `docker-compose.yml`: one `gateway` Uvicorn container, `restart: unless-stopped`, depends on Redis and `postgres:17-alpine` health, `POSTGRES_USER=aigateway` (bootstrap superuser by image initialization); database name `aigateway`. Gateway's `.env` values and actual connection principal are **not** established. Host port 8080:8000; source bind mount `./src:/app/src`. PostgreSQL is on an internal Compose bridge, but that alone does **not** establish an exclusive, non-bypassable DB network path.
- `se/config/default.yaml` enables the **SQLite** storage driver as required; `main.py:bootstrap_storage()` binds its main UoW to that driver. Never infer that the application UoW uses PostgreSQL. `ASSISTANT_ALEMBIC_DATABASE_URL` selects the independent Alembic SQL target. Optional Skill publication uses a **separate** `ASSISTANT_SKILL_PUBLICATION_DATABASE_URL` PostgreSQL engine in startup and an independent Skill-publication Alembic environment. Both may or may not point to the AIC target cluster/database in a real deployment; verify physically.
- `Dockerfile` launches Uvicorn directly; `main.py` bootstrap starts an H1-A recovery sweep/periodic worker before serving; application shutdown quiesces recovery, supervisor, eventing, coordinator and runtime. These guards do not persist an all-client database admission state across crash/restart, do not fence external migrators and cannot replace network-level exclusion.
- `/health` is unconditional and `/ready` checks cache/providers, not a production DB admission epoch. DB `pg_isready` tests PostgreSQL availability, **not** barrier authorization. Neither may reopen traffic.
- Main `sql/env.py` is a once-only Alembic invocation with `NullPool` (candidate PR494 adds separate PG version-width preflight). PR504 31b obtains schema-qualified triple-table `ACCESS EXCLUSIVE NOWAIT`: it only fails fast under contention. `55P03` means old, vulnerable 31a may remain. Independent PR511 PG17 Linux/Windows hard-kill evidence is disposable CI, not production isolation.
- Repo source cannot identify live DSNs, external cron/ETL/reporting jobs, PgBouncer, Kubernetes workloads, supervisors, replica/writer endpoints, local Unix sockets, SSH tunnels, direct admin clients, backup agents, logical replication clients, host firewall or DB operator. **All actual topology/role coverage remains UNKNOWN until the target operator's signed inventory.** Do not claim exhaustive production inspection from this file.

## 2. Required security invariant

Let `T` be the positively identified target cluster + DB + writer endpoint, `E` a unique monotonic deployment generation, `C` every possible client channel into `T`, `M` a separately authenticated migration actor, and `V` the independent read-only verifier.

**No production AIC 31b migration may start unless a durable, external-to-T and externally enforced barrier for E is CLOSED across all C, existing transactions/sessions are drained (with specifically assessed privileged/maintenance exceptions), and only M/V can connect.** This state must survive gateway restart, DB restart, operator-controller crash, supervisor auto-restart, credential refresh, and a stale release request. A stale or ambiguous barrier epoch MUST NOT transition CLOSED→OPEN.

- All app APIs/WS, recovery sweeps, task/fork/retry/aggregate admission, worker/job queues, direct SQL clients, any pooler and any legacy CLI are denied new work or new target-DB connections **before** a migration credential becomes usable. The external barrier, not FastAPI middleware, is the final admission authority.
- No superuser, table owner, `CREATEROLE`/ADMIN-option principal, `SET ROLE`-reachable NOLOGIN owner, alternate account, replication/DDL actor or direct network path may silently bypass C. If any is not completely controlled, STOP. Never treat `NOLOGIN` as a drain: existing connections survive.
- No destructive role/privilege, `pg_terminate_backend`, cluster restart or network mutation is authorized by this design. DBA must separately approve environment-specific enforcement and rollback. Existing superuser used by the application is a **hard design blocker** until separately isolated/replaced or the whole endpoint is externally fenced.
- Barrier release requires (i) immutable reviewed artifact/source graph/commit digest, (ii) a fresh DB physical readthrough of version `31b`, all four exact trigger attachments and three exact function search_path pins, (iii) restored least-privilege identity separation, and (iv) two different authenticated actors, deployment operator and security/DB reviewer, signing E. A liveness probe or elapsed timeout cannot release.

## 3. Layered enforcement and deployment topology

1. **D0 — External durable authority:** deploy-controller-owned state stored outside the target DB, separate from Redis/app memory, with append-only signed events and monotonic E. Protect against rollback to old OPEN snapshot. Only designated operator can begin a CLOSED episode; release requires distinct reviewer attestations. Unknown/unavailable state => deny new admission. No unconditional TTL expiry, scheduled auto-unfreeze, or restart bootstrap into OPEN.
2. **D1 — Network/endpoint edge (mandatory):** server-side firewall / Kubernetes NetworkPolicy **plus a verified default-deny topology** (or equivalent host/network enforcement) that keeps only separately authenticated, time-bounded migration M and verifier V reachable. Inventory IPv4+IPv6, direct pod/host networking, DB loopback & Unix sockets, internal replicas/primaries, pooler listener and direct DB endpoints. A network rule that can be bypassed by an existing trusted superuser socket fails D1; that socket must be independently privileged and audited. Unknown network paths = HOLD.
3. **D2 — Role/DB identity (defense in depth):** separate app/read/worker/pooler/legacy/migrator/observer/admin login identities. Appropriately scoped `NOLOGIN` and `REVOKE CONNECT` may prevent fresh ordinary role connections but do NOT evict existing sessions or constrain superusers. Inspect `pg_auth_members` ADMIN/INHERIT/SET, effective `pg_has_role`, superuser/CREATEROLE, database/schema/table/function owners, default/PUBLIC ACL, TRIGGER/TRUNCATE on `users`/`aic_instance_reservations`/`aic_instance_live`. An app identity with TRUNCATE/owner rights can erase durable RETIRED markers, including via CASCADE. Do not conflate capability to `SET ROLE` into a NOLOGIN owner with inability to login as that owner.
4. **D3 — Runtime bootstrap (secondary):** a generation-bound admission check before `main.py:lifespan()` invokes `bootstrap_storage`, Skill publication, runtime kernel or H1-A recovery; on CLOSED/UNKNOWN fail startup or provide only maintenance endpoints. All existing engines/pools/workers must dispose/drain before D1 witness. Add an admission check at root/fork/retry/resume and UoW creation **only after exact owner source-scope agreement**. Never allow runtime self-reopen based on process-local memory, `/health`, `/ready`, a successful DB connection or one good session.
5. **D4 — Migration and verification:** one immutable composed Alembic CLI under M, target **explicitly** `31b_aic0_pg_guard_forward` not generic `head`; validate live cluster identity, current revision, reviewed PR494 full SHA and env.py matching blob, PR496 pin, PR504 31b candidate, exact DAG and absence of other migration contenders. PR494 version-width preflight may commit before 31b and MUST be recorded separately from the 31b transaction. On 55P03/timeout/killed CLI: keep D0/D1 closed; inspect under V on a new connection; explicit operator decision only, no automatic retry.
6. **D5 — Reopen:** only after exact physical readthrough, zero unowned sessions/locks, no corrupt RETIRED→LIVE, ownership/ACL parity, complete independent evidence and two-actor E release. Reenable in an explicit sequence (workers/migrators only as approved, DB clients, ingress) while recording every changed network/role control. If re-enable partially fails, reclose and quarantine; never permit reopen from a stale E or automatic container restart.

**Deployment-specific caveat:** for Docker Compose, do not assume `docker compose stop gateway` (one host only), Compose `restart: unless-stopped`, port 8080, `pg_isready` or the Compose bridge closes every external SQL path. A native production implementation must obtain the actual container/host/DB topology and ensure any orchestrator reconciliation cannot start a work-capable gateway while D0/D1 are closed. For Kubernetes, the same principle applies to Deployment, Jobs/CronJobs, HPA, init containers and any privileged host-network pod; no Kubernetes-specific implementation is claimed to exist.

## 4. State machine (external controller, never stored solely in target PG)

| State | Enter / source of authority | Allowed behavior | Forbidden behavior |
| --- | --- | --- | --- |
| `LOCKED` default | First enrollment or controller uncertainty | Inventory and observer-only safety check | App DB admission; migration; release |
| `INVENTORIED` | Signed cluster, principals, all paths, controllers, artifact E | Prepare network/role barrier with no worker admission | Call it quiescent |
| `FENCING` | Durable E seal written **before** applying network cut | Deny ingress, freeze restarts/queues, close all non-M/V paths | Begin migration; unfreeze on timeout |
| `DRAINED` | Physical network deny proof and independent DB session/lock/2PC/pooler/replication observations | Single-use authorization for M | Any non-M/V reconnect |
| `MIGRATING` | E-bound source/DB/migrator identity and one-way attempt receipt | Exactly one CLI to 31b; read-only observe | Parallel migration; auto retry |
| `VERIFYING` | CLI stopped, transaction outcome freshly read | V verifies physical catalogs/ACL/version/corruption | Auto reopen; treat exit 0 as proof |
| `RELEASE_AUTHORIZED` | Two actor-distinct E-bound approvals + D1 verification | Controlled reopen of explicitly approved paths | Reopen unknown roles/auto restarts |
| `OPEN` | Audited release completed | Normal admitted traffic | Stale E replay |
| `QUARANTINED` | Any crash, SQLSTATE 55P03, mismatched witness, stale E, unexplained session or missing observer | Read-only investigation under barrier, explicitly approved recovery | Migration retry/reopen without fresh E review |

Atomic ordering: persist E seal -> enforce deny at each endpoint -> independently prove deny/drain -> permit M. A controller crash at any point stays CLOSED. Migrator exit is NOT the authorization to OPEN. New E must not supersede unresolved earlier E by merely resetting the state.

## 5. Role and restart coverage — source-backed inventory, not fictitious production attestations

| Route / role / restart source | Verified source observation | Required real deployment witness | Disposition |
| --- | --- | --- | --- |
| PostgreSQL bootstrap `aigateway` | Compose `POSTGRES_USER=aigateway`; image initializes a superuser | Record actual server-side `rolsuper`, owner graph, bootstrap credential custody, whether any app DSN uses it | HOLD |
| API/WS gateway | Uvicorn starts from Dockerfile; Compose `unless-stopped`, API health/readiness do not gate AIC | Ingress controller deny, container restart suppression, process startup DENY and new-work negative | HOLD |
| Default SQL UoW | Required SQLite driver by default.yaml; main binds it | Confirm deployment-specific override and **whether UoW even targets PG**; no inferred PG admission | HOLD |
| AIC Alembic migrator | `ASSISTANT_ALEMBIC_DATABASE_URL`, NullPool, PR494 independent preflight | Full immutable DSN target/driver/query/options resolution (redacted), isolated M identity and unique CLI actor | HOLD |
| Skill publication PostgreSQL | Optional independent `ASSISTANT_SKILL_PUBLICATION_DATABASE_URL` and standalone Alembic | Prove cluster/db relation to T, startup side effects, grants, pool disposal and independent migrator admission | HOLD |
| R12 H1-A recovery | `H1ARecoveryControlPlane.start` before traffic, periodic worker; `quiesce` at graceful shutdown | Kill/restart during FENCING/MIGRATING; no stale RUNNING sweep / new execution / DB write | HOLD |
| Agent supervisor / Task fork/retry/aggregate | `main.py` creates work and process-local task runners | Reject all activations before durable admission; no winner/loser side effects while E sealed | HOLD |
| Redis/event/background jobs | Redis service and EventManager in bootstrap; periodic services may persist | Confirm queues, delayed jobs, retry policies, out-of-process workers and their DSNs | HOLD |
| Direct DB readers/pools/reporting | Not enumerated in repo; SQLAlchemy PG pool for optional skill publication | Connection inventory, `pg_stat_activity`, `pg_locks`, poolers/proxies, reporting, replicas, backends idle in transaction | HOLD |
| Cron/systemd/K8s/external manual CLI | Not established by repository Compose | Scan real deployment scheduler/supervisor inventory and block recreate/auto-migrate paths | HOLD |
| Owner/admin/superuser/replication | Compose bootstrap superuser only; actual users unknown | All login/SET/inherit/admin/CREATEROLE paths, Unix/TCP/SSL/IPv6, direct host access, logical replication | HOLD |

**Principals are not interchangeable:** physical DB `session_user`, `current_user`, role membership, connection pool login, application auth principal, operator identity and workflow actor need separate evidence. Preserve redacted DSN fingerprints and server endpoint/cluster identity; never publish passwords.

## 6. Operator API / proposed exact implementation units (NO implementation approval yet)

Proposed **candidate** source manifest (paths are proposals, not claimed ownership, and may be revised after #156/#426/#381 agreement):
- ADD `ops/aic31b/admission_controller.py`: external CLI orchestrating E state transition and verifying recorded external infrastructure enforcement; does not access app code or auto-deploy changes.
- ADD `ops/aic31b/role_and_session_inventory.py`: read-only PG17 catalog/witness collector; protects secret values and connects exclusively as V.
- ADD `ops/aic31b/restart_path_inventory.py`: compose/service-manager/Kubernetes adapter discovery with explicit Unsupported/Unknown=DENY; no assumed topology.
- ADD `ops/aic31b/immutable_candidate.py`: exact commit/tree/artifact, DAG and PR494 vs PR506 blob validator; no short-SHA resolution or inferred ancestry.
- ADD `ops/aic31b/barrier_contract.py`: typed state, monotonic E receipt and capability-split operator/reviewer approvals, external persistent storage interface.
- ADD `se/tests/integration/test_aic31b_production_admission_barrier.py`: real PG17 multi-process negative tests (opt-in isolated fixtures only).
- ADD `.github/workflows/aic31b-operator-barrier-pg17-linux-windows.yml`: dedicated Linux/native Windows no-skip job; no production secrets or runner privileges.
- **MOD (separate owner-consented stage only)** `se/src/main.py`, `se/src/transport/gateway/api/v1/health_router.py` and exact task/recovery/driver seams *after independent source-scope and concurrency review*. Do not silently modify them in a docs or migration PR. Avoid changing PR504's two-file 31b migration or PR494 env.py without an explicit new security finding and independent review.

Suggested interface:
```text
inspect(target_id) -> Witness                  # read-only; never admits
seal(target_id, expected_external_epoch, ticket, operator) -> E
freeze(E, enumerated_paths, proof) -> CLOSED
witness_drain(E, db_observation, network_observation) -> DRAINED
authorize_one_migration(E, immutable_digest, M) -> single-use receipt
record_attempt(E, receipt, outcome) -> VERIFYING | QUARANTINED
verify(E, catalog_proof, security_review) -> RELEASE_AUTHORIZED | QUARANTINED
release(E, operator_signature, distinct_reviewer_signature) -> controlled OPEN
```
Use compare-and-swap on the **external** durable authority with signed audit log, immutable target identity, idempotent repeated `seal`/verify, no stale generations, and explicit all-or-nothing role/network witness. A receipt must not become a bearer secret to arbitrary app processes. Failure to contact authority => deny; a mere file or environment variable does not prove authenticity.

## 7. RED-first regression matrix (must run BEFORE production source FINAL)

| Test | Fault / race | Expected |
| --- | --- | --- |
| B01 | Start gateway while barrier CLOSED/UNKNOWN, even with successful `/health` and `pg_isready` | 0 admitted API/WS/Agent DB work |
| B02 | Compose/K8s worker auto-restart and queued job redelivery across FENCING→DRAINED | 0 new AIC writes / new SQL sessions |
| B03 | PG restart, controller crash, external state store failure, stale E OPEN replay | Network admission remains physically DENY; no auto reopen |
| B04 | Existing pooled reader/writer and idle-in-transaction, plus direct legacy CLI | Cannot reach DRAINED while live sessions/locks; migration cannot start |
| B05 | `NOLOGIN` set but old session survives, or `REVOKE CONNECT` only | Refuse false DRAINED claim |
| B06 | App `SET ROLE` to NOLOGIN owner, inherited rights, ADMIN-option self-grant, superuser/CREATEROLE path | O3 STOP / 0 unauthorized TRUNCATE/DDL |
| B07 | `TRUNCATE ... CASCADE` against durable RETIRED marker tables | DENY for every app/worker/reader/legacy effective principal |
| B08 | PG URI query host override, Unix socket, IPv6, alternate cluster/DB, replica/pooler, skill-publication DSN | Uninventoried endpoint => STOP |
| B09 | Concurrent migrators and uncooperative legacy CLI (bypassing advisory lock) | Exactly one E-bound CLI; all other channels blocked |
| B10 | `NOWAIT` 55P03 on any of three tables | No retry/reopen, 31a physical proof preserved, QUARANTINED |
| B11 | OS kill before 31b commit, then DB/controller restart | Rollback of revision/functions/triggers, isolation retained |
| B12 | OS kill after 31b commit, then lost ACK | New V readthrough validates 31b; no blind retry |
| B13 | PR494 preflight width DDL committed; 31b transaction aborted | Record independent version-width metadata state and 31a revision; HOLD |
| B14 | Corrupt or disabled trigger, wrong tgrelid/tgfoid/attnum/WHEN/args/proconfig | STOP, no auto patch/reopen |
| B15 | Only one actor signs; stale approval; epoch collision; permission revoked; role rotated | No release; two current distinct authorities required |
| B16 | Partial network reopen or supervisor starts between two reopen steps | Revert to quarantine, no split-brain OPEN |
| B17 | SQLite-default-only deployment / Skill PG configured to other DB | Correctly classify unrelated DB; never assume same target or omit PG actor |
| B18 | Linux + native Windows PG17.11, strict negative assertions, raw session/ACL/role and CI artifacts | Both true platform jobs mandatory, 0 selected skips/failures |

CI-local application `NOLOGIN` or a mockable network adapter are **not** production enforcement evidence. Record proofs from separate OS processes and PostgreSQL physical catalogs; inject fault before/after commit and during separate controller/DB restarts. Staging must resemble the true topology and independent principal set. Production rollout still requires named operation and signed real inventory.

## 8. Gate acceptance and sequencing

- **P0 DESIGN REVIEW:** #381, #156 and #426 agree exact target/environment topology, source conflict map, positive app/worker/reader/legacy role identities, operator ownership and externally durable freeze/reopen semantics.
- **P1 EXTERNAL INFRA BARRIER:** implement and validate external D0/D1 with restart proof; neither application nor migrations run during unknown state. No database privilege mutation before DBA-approved isolation.
- **P2 APP/ROLE INTEGRATION:** separately scoped runtime/source stage and exact role grants; enforce on every admission seam; test multiple processes, old sessions, restart, `SET ROLE`, TRUNCATE and poolers.
- **P3 COMPOSED MIGRATION:** immutable PR494+496+504+506 graph, fully reviewed full SHA, old 31a→31b PG17 Linux/native Windows; preserve PR511 interruption proof. Version-width and 31b commit are distinct durable events.
- **P4 REAL OPERATOR WITNESS:** all O1–O9 PR514 operator evidence, actual staging/prod identity readthrough, security reviewer distinct from implementer, signed allowed reopen.
- **P5 TBO-4 namespace/erasure:** #381 TBO 32a **unreserved** until AIC canonical DAG and #426/#141/#74/#15 R1/R2 retention-erasure decision; do not conflate AIC migration rollout with TBO-4 durable activation authority.

**Current gate:** `ADMISSION_BARRIER=DESIGN_ONLY`, `ROLE_INVENTORY=SOURCE_PARTIAL/PRODUCTION_UNKNOWN`, `RESTART_PATHS=SOURCE_PARTIAL/EXTERNAL_UNKNOWN`, `AIC31B_ROLLOUT=HOLD_UNENFORCED`, `PRODUCTION_FINAL=HOLD`, `MERGE=NONE`, `AGENT_NATIVE=DENY`.
