# AIC 31b — PostgreSQL 17 enforced-quiescence operator packet

**Status:** DESIGN / DRAFT / NOT VERIFIED AGAINST PRODUCTION / NOT AUTHORIZATION TO EXECUTE.
**Owners:** #156 CRT, #426 AIC lifecycle/erasure, #381 Alembic/deployment custody.
**Candidate graph:** PR467@6edd86a0 → PR496@868546fd → PR500@66c7ec8f → PR504@bb0d110e → PR506@3e3f34b → PR509@a75d7df → PR511@42485ba8. PR494@`f7efcd63c479626d43733ad836fc092db84a8506` (full independently fetchable source commit) is composed by **byte-identical `env.py`** into PR506@`3e3f34b0892d4632fd2497da2ee312a72f6b4e9f`, without copying the PR494 commit object into PR506's history. Both trees resolve `se/src/infrastructure/storage/migrations/sql/env.py` to exact Git blob `9880e0b3b8ea22b1bc473a53a3d3357d3224ce9e`. This is a *composition/content* assertion, not false commit ancestry. None of these PRs is merged to main. Current canonical main@af0feb268b59cc9828c536eda1629c3a64c39bed remains at Alembic 30a (owner-verified). **Do not run against production from unmerged code.**

## 0. Security assertion and unproven environment

The source-shape fix and actual PG17/native-Windows/Ubuntu CI do **not** provide production traffic exclusion. PR504's 31b takes transaction-held schema-qualified ACCESS EXCLUSIVE NOWAIT locks on physical public.users, public.aic_instance_reservations and public.aic_instance_live. Even a normal SELECT holding ACCESS SHARE can cause SQLSTATE 55P03; the 31b transactional upgrade rolls back and the old, vulnerable, installed 31a functions can remain. A cooperating advisory lock in PR494 only serializes participants using that lock, not legacy migrators, raw DDL or application readers/writers.

Current main docker-compose.yml specifies postgres:17-alpine with POSTGRES_USER=aigateway and POSTGRES_DB=aigateway, one gateway service, and redis. The official PostgreSQL image initializes POSTGRES_USER as a PostgreSQL superuser; this Compose specification does NOT establish a separate non-privileged application connection, nor does it identify any external gateways, workers, cron jobs, read replicas, migration clients, backups or poolers. The gateway env_file points at .env but its production values are NOT known. **Treat production role inventory, DSNs, topology and actual operator authority as UNKNOWN.** Do not print passwords or DSNs with embedded credentials into evidence.

Do not assume STOP of one Compose gateway, role NOLOGIN alone, REVOKE CONNECT, a load balancer maintenance banner, an advisory lock, health-check success, or PG17 CI means a complete admission barrier. Existing sessions survive NOLOGIN; superusers/other roles and connection pools may bypass a single-app-role control. Maintain a separately enforced deny-new-work/deny-new-connection gate across ALL channels until explicit release.

## 1. Go/no-go ownership record (must be signed BEFORE production)

| Gate | Required evidence | Failure disposition |
|---|---|---|
| O1 Named accountable operator | Named human/team, escalation contact, maintenance window ID, operator/DB reviewer distinct from implementer, target DB identity and immutable approved build digest | HOLD |
| O2 Ingress/worker inventory | Every HTTP/WS gateway, scheduler, R12 recovery worker, async task, reader, reporting client, connection pooler, replica/writer endpoint, admin/manual/legacy migrator and automatic restart controller enumerated with identity/owner | HOLD |
| O3 SQL principals | Distinct app/read/worker/migration/admin/legacy roles; enumerate `pg_auth_members` with ADMIN/INHERIT/SET options, transitive `pg_has_role(...,'SET'/'USAGE')`, superuser and CREATEROLE capability, database/schema/table/function ownership and explicit/PUBLIC/default ACLs, LOGIN/CONNECT/TEMP/schema CREATE/table TRIGGER/TRUNCATE/DDL; no membership-based bypass allowed | HOLD |
| O4 Enforced admission + drain | External deploy/DB admission controller rejects new work AND connections for every identified principal; physical sessions and long-lived transactions drained; no autostart/race re-entry | HOLD |
| O5 Immutable target | Approved artifact includes exact reviewed PR494 preflight, PR496 secure 31a, PR504 repaired 31b; unique graph 30a→31a→31b, no sibling 32a and no hidden Alembic stamps or manual ALTER | HOLD |
| O6 Quiescent single execution | A single privileged Alembic CLI upgrades only to 31b under the same global exclusion; no automatic retry, no downgrade | HOLD |
| O7 Verification before reopen | Independent connection sees 31b revision, physical four trigger bindings, three pinned function configs, RETIRED marker nonreuse/known-corruption refusal, privilege audit and no unknown session; signed reviewer evidence | HOLD |
| O8 Crash, timeout, 55P03 | On any ambiguity, maintain isolation and block application/native Agent; inspect actual commit/stamp/guards on new connection and retry only by explicit operator decision after new quiescence | HOLD |
| O9 Data lifecycle handoff | #426/#141/#74/#15 Task/account erasure/retention disposition separate from AIC DB repair; #381 TBO P0-S 32a not auto-reserved | HOLD |

No auto-promote, no automatic production deployment, no automatic traffic reopen.

## 2. Read-only preflight evidence (DBA-approved observer on the ACTUAL target)

These queries perform no writes. Run with a verified privileged observer that has visibility into all sessions/roles; restricted pg_stat_activity visibility is NOT sufficient. Check that current_database(), current_user, server version, cluster identity and network target match the approved production inventory BEFORE collecting or acting on any result. Save redacted output with reviewer and timestamp; do not include secrets.

~~~sql
SELECT current_database() AS db, current_user AS observer,
       current_setting('server_version_num') AS server_version,
       current_setting('search_path') AS search_path,
       pg_catalog.pg_postmaster_start_time() AS started_at;

SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
       rolinherit, rolcanlogin, rolbypassrls
  FROM pg_catalog.pg_roles ORDER BY rolname;

-- Membership grants alone do not describe the full ability to SET ROLE:
-- inspect the direct graph AND all transitively reachable roles.
SELECT member.rolname AS member_role, parent.rolname AS granted_role,
       grantor.rolname AS grantor_role, m.admin_option,
       m.inherit_option, m.set_option
  FROM pg_catalog.pg_auth_members m
  JOIN pg_catalog.pg_roles member ON member.oid = m.member
  JOIN pg_catalog.pg_roles parent ON parent.oid = m.roleid
  LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid = m.grantor
 ORDER BY member.rolname, parent.rolname;

SELECT login.rolname AS login_role, target.rolname AS reachable_role,
       pg_catalog.pg_has_role(login.oid, target.oid, 'MEMBER') AS member,
       pg_catalog.pg_has_role(login.oid, target.oid, 'MEMBER WITH ADMIN OPTION') AS can_admin_membership,
       pg_catalog.pg_has_role(login.oid, target.oid, 'USAGE') AS inherits_authority,
       pg_catalog.pg_has_role(login.oid, target.oid, 'SET') AS can_set_role
  FROM pg_catalog.pg_roles login
 CROSS JOIN pg_catalog.pg_roles target
 WHERE login.rolcanlogin AND login.oid <> target.oid
   AND (pg_catalog.pg_has_role(login.oid, target.oid, 'MEMBER')
        OR pg_catalog.pg_has_role(login.oid, target.oid, 'USAGE')
        OR pg_catalog.pg_has_role(login.oid, target.oid, 'SET'))
 ORDER BY login.rolname, target.rolname;

-- Owners, ACLs and default grants must be reconciled with the graph above.
-- A role with SET ROLE to a NOLOGIN owner can exercise ownership privileges.
SELECT d.datname, d.datdba::pg_catalog.regrole AS database_owner, d.datacl
  FROM pg_catalog.pg_database d
 WHERE d.datname = current_database();

SELECT n.nspname, n.nspowner::pg_catalog.regrole AS schema_owner, n.nspacl
  FROM pg_catalog.pg_namespace n WHERE n.nspname = 'public';

-- AIC tables may not exist at 30a; execute this table inventory only
-- after physical AIC relations exist at 31a or 31b.
SELECT c.relname, c.relkind, c.relowner::pg_catalog.regrole AS table_owner,
       c.relacl
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public'
   AND c.relname IN ('users', 'aic_instance_reservations', 'aic_instance_live',
                     'alembic_version')
 ORDER BY c.relname;

SELECT p.proname, p.proowner::pg_catalog.regrole AS function_owner, p.proacl
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public'
   AND p.proname IN ('aic_id_guard','aic_live_guard','aic_user_guard')
 ORDER BY p.proname;

SELECT defaclrole::pg_catalog.regrole AS default_acl_owner,
       defaclnamespace::pg_catalog.regnamespace AS default_acl_schema,
       defaclobjtype, defaclacl
  FROM pg_catalog.pg_default_acl
 WHERE defaclnamespace=0
    OR defaclnamespace='public'::pg_catalog.regnamespace
 ORDER BY defaclrole, defaclnamespace, defaclobjtype;

SELECT pid, usename, application_name, client_addr, state,
       backend_type, backend_start, xact_start, wait_event_type, wait_event
  FROM pg_catalog.pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_catalog.pg_backend_pid()
 ORDER BY xact_start NULLS LAST, backend_start;

SELECT r.rolname,
       has_database_privilege(r.rolname, current_database(), 'CONNECT') AS can_connect,
       has_database_privilege(r.rolname, current_database(), 'TEMPORARY') AS can_temp,
       has_schema_privilege(r.rolname, 'public', 'CREATE') AS can_create_public,
       has_table_privilege(r.rolname, 'public.users', 'TRIGGER') AS can_trigger_users,
       has_table_privilege(r.rolname, 'public.users', 'TRUNCATE') AS can_truncate_users
  FROM pg_catalog.pg_roles r
 WHERE r.rolcanlogin ORDER BY r.rolname;

SELECT a.pid, a.usename, a.application_name, a.state,
       l.locktype, l.mode, l.granted, l.relation::pg_catalog.regclass AS relation
  FROM pg_catalog.pg_locks l
  JOIN pg_catalog.pg_stat_activity a ON a.pid=l.pid
 WHERE a.datname=current_database()
   AND l.relation IN ('public.users'::pg_catalog.regclass,
                      'public.aic_instance_reservations'::pg_catalog.regclass,
                      'public.aic_instance_live'::pg_catalog.regclass)
 ORDER BY a.pid, l.mode;

SELECT version_num FROM public.alembic_version;
SELECT table_schema, table_name, column_name, character_maximum_length
  FROM information_schema.columns
 WHERE table_schema='public' AND table_name='alembic_version'
   AND column_name='version_num';

SELECT p.proname, n.nspname AS function_schema, p.prosecdef, p.proconfig,
       p.proowner::pg_catalog.regrole AS function_owner
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN
       ('aic_id_guard','aic_live_guard','aic_user_guard')
 ORDER BY p.proname;

SELECT t.tgname, n.nspname AS relation_schema, c.relname AS relation_name,
       t.tgtype, t.tgenabled, t.tgattr::text, t.tgqual IS NULL AS no_when,
       t.tgnargs, octet_length(t.tgargs) AS args_bytes,
       pn.nspname AS function_schema, p.proname AS function_name
  FROM pg_catalog.pg_trigger t
  JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
  JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
 WHERE n.nspname='public' AND t.tgname IN
       ('trg_aic_id_guard','trg_aic_live_guard',
        'trg_aic_user_delete','trg_aic_user_rekey')
 ORDER BY t.tgname;
~~~

**Preflight interpretation:** a database currently stamped 30a does not yet have 31a AIC tables. Do not run their regclass or trigger queries prematurely; mark these as NOT APPLICABLE until a target schema is actually present. For already-stamped 31a, check all three relations exist and require intact physical attachments; corrupt or disabled triggers or unexpected RETIRED→LIVE data is STOP, not an opportunity to silently repair data. Role privilege and ownership queries must be reviewed **together**: a NOLOGIN owner can be reached by `SET ROLE`; a superuser can bypass ordinary ACLs; ownership permits DDL independently of GRANTs. Also, `CREATEROLE` with membership `ADMIN OPTION` may permit an actor to grant itself a new `SET`/`INHERIT` path even when its CURRENT `pg_has_role(...,'SET')` is false. Review both current authority and the ability to change memberships; block grants/role administration as part of admission freeze. Default/PUBLIC grants and external principals require environment-specific reconciliation. A missing or ambiguous membership edge, inherited/SET/ADMIN option, CREATEROLE mutation path, owner/ACL or external login is O3=HOLD, not PASS.

## 2A. Immutable PR494 source-provenance and composition verification

Reviewer pin set (all SHA-1 Git object identifiers; **do not treat PR494 as an ancestor of PR506**):

| Object | Full pinned SHA | Required check |
|---|---|---|
| PR494 source commit | `f7efcd63c479626d43733ad836fc092db84a8506` | Fetch PR494's separate named branch; verify the exact commit, not a guessed short ref |
| PR494 `env.py` blob | `9880e0b3b8ea22b1bc473a53a3d3357d3224ce9e` | Resolve `PR494_SHA:se/src/infrastructure/storage/migrations/sql/env.py` |
| PR506 composed commit | `3e3f34b0892d4632fd2497da2ee312a72f6b4e9f` | Already in the PR514 ancestry; confirm object exists |
| PR506 `env.py` blob | `9880e0b3b8ea22b1bc473a53a3d3357d3224ce9e` | Resolve `PR506_SHA:path`; compare full blob equality |

Read-only example on a separately trusted full-history checkout (requires repository read access):

~~~bash
git fetch origin refs/heads/feature/156-pg-alembic-version-width-20261012
test "$(git rev-parse FETCH_HEAD)" = "f7efcd63c479626d43733ad836fc092db84a8506"
git cat-file -e "f7efcd63c479626d43733ad836fc092db84a8506^{commit}"
test "$(git rev-parse 'f7efcd63c479626d43733ad836fc092db84a8506:se/src/infrastructure/storage/migrations/sql/env.py')" = "9880e0b3b8ea22b1bc473a53a3d3357d3224ce9e"
git cat-file -e "3e3f34b0892d4632fd2497da2ee312a72f6b4e9f^{commit}"
test "$(git rev-parse '3e3f34b0892d4632fd2497da2ee312a72f6b4e9f:se/src/infrastructure/storage/migrations/sql/env.py')" = "9880e0b3b8ea22b1bc473a53a3d3357d3224ce9e"
~~~

The **GitHub PR494 HEAD and PR506 file blobs were independently re-fetched and matched** during preparation of this packet. If the PR494 branch advances, the HEAD comparison MUST fail closed until the owner records a new pin. If checkout is shallow and PR506 commit is not present, fetch the separately named PR506 branch, re-check its full SHA, then repeat. No implicit source inheritance, cherry-pick, or deployment permission follows from this provenance proof.

## 3. Concrete operator sequence — not yet operationally approved

1. **Freeze:** obtain signed O1–O5; record backup/restore readiness and trusted artifact digest; freeze every migrator including legacy tools. Lock down workload rollout controllers to prevent unattended restarts. Issue a maintenance token in the *external deployment controller*, not the application DB; native Agent remains DENY.
2. **Stop new work and new DB sessions:** enforce at the gateway, worker queues, schedulers, poolers and DB role/connection layer for all inventoried identities. Merely changing LOGIN, a gateway status flag or one Compose service is not a full barrier. Reject all unknown clients.
3. **Drain:** finish/cancel preexisting work by approved procedures and physically verify sessions/transactions/locks from another privileged connection. Unrecognized active or idle-in-transaction session = HOLD. Never silently terminate production sessions or roles; that is an independently approved operator action.
4. **Verify migration graph and identity:** run a privileged fresh CLI with exact reviewed combined source after **Section 2A full-commit and blob provenance checks PASS** and no pending TBO/CTX competing revision; the path target is explicitly 31b_aic0_pg_guard_forward, NOT an unspecified head. PR494 online PostgreSQL public-only preflight must be present; otherwise HOLD. No stamp, manual version-table widening, hidden retry, or security downgrade.
5. **One migration attempt:** while barrier is maintained, run the approved CLI. If 55P03 or any error occurs, retain isolation and inspect actual revision on a new connection; never reopen automatically, never issue an opportunistic retry.
6. **Post-commit read-through:** on a separate fresh connection re-check the version row, 3 pinned pg_proc.proconfig entries, four correct physical trigger OIDs/relations/functions/events/enablement/UPDATE OF users.id, no existing RETIRED→LIVE corruption and role ownership/ACLs. The exact shape-checking algorithm in PR504 is the acceptance oracle. Existing test-only hard-exit proof on PR511 is supplemental, not a production write probe.
7. **Approve reopen:** only named operator AND independent security/DB reviewer may release admission in a controlled sequence. If any query is ambiguous or evidence incomplete, keep isolation. On error, do not automatically enable native tools, TBO activation or R8 execution.
8. **Observe:** after controlled reopen, check connection identities, health and unexpected migration activity. Anomaly => reapply previously approved admission barrier and incident protocol; do not rerun migration speculatively.

## 4. Negative acceptance matrix

| Failure cut | Expected durable state | Required action |
|---|---|---|
| Traffic remains during triple-table NOWAIT | 31b aborts with 55P03; old stamp/guards stay unchanged | Remain isolated; find reader/writer; re-quiesce before separately approved fresh CLI |
| Before-commit OS kill during 31b | PostgreSQL rolls back DDL/revision atomically; 31a remains | Confirm on a fresh connection; no implicit retry/reopen |
| After-commit OS kill | 31b revision and trusted functions survive | Confirm catalog and all guard checks before release |
| Missing/wrong/disabled trigger, tgattr/WHEN/args drift | Migration refuses modification | Remain isolated, escalate incident; never auto-fix or stamp |
| Already-corrupt RETIRED→LIVE data | Migration refuses repaired-security acceptance | Hold; separate investigated retention/remediation plan |
| Unknown DB principal, owner role or ability to bypass admission | No operational quiescence assertion | Hold and complete inventory; forbid unilateral role changes |
| Legacy migrator not honoring advisory lock | Potential concurrent DDL | Block externally, do not rely on PR494 lock |
| Ambiguous post-commit outcome or reduced pg_stat_activity visibility | Cannot verify safety | Hold; new privileged observer and independent review |
| Missing retention/erasure decision | TBO P0-S and 32a remain unreleased | #426/#141/#74/#15 signoff; no silent R1/R2 choice |

## 5. Evidence artifact to submit for independent review

Redacted operator identity and change ticket; exact approved source/tree SHA; physical cluster/database identity (no credentials); current Alembic revision and unique graph; signed completeness of all service/role inventories; how the admission barrier denies each enumerated actor including supervisors and legacy migrators; actual before/after session/transaction/lock snapshots; direct/transitive role membership incl. ADMIN/SET/INHERIT, actual database/schema/table/function ownership and ACL/PUBLIC/default grants, explicit superuser bypass assessment; first CLI exit/SQLSTATE; exact post-commit trigger/proc/catalog snapshots; interruption/lock-negative rehearsal on an equivalent staging deployment topology Linux/native Windows; rollback/quarantine and reopening decision logs; signed independent operational/security review.

**Gate remains:** ENFORCED_QUIESCENCE_SELECTED_CONDITIONALLY / NOT IMPLEMENTED IN PRODUCTION. This is an evidence-and-design aid, not executable code, not a deployment, not a production PASS, not erasure approval, not merge or native execution permission.
