# Direct Terminal permission upgrade — P1 remediation

**Status:** proposed non-production remediation on PR #562 successor. This runbook is
not permission to deploy, run production migrations, or grant production roles.
Never interpret role `admin`, `admin:write`, an API-key `scopes={"*"}` or
`auth_type="admin_key"` as an implicit right to invoke a terminal.

## Why this is required

`AuthorizationService` enforces `terminal:execute` for the canonical
`terminal.run` and `terminal.launch` capabilities. JWT and API-key
authentication obtain effective permissions from durable
`roles -> role_permissions -> permissions` records; signing an admin JWT
or creating an admin API key does not create that permission. Historical
installations may lack it. Prior signed-JWT E2E tests explicitly seeded both
permission and test-role assignment and therefore did not cover upgrades.

## Upgrade procedure (operator-controlled)

1. Back up the database and verify target environment. Confirm that
   `ASSISTANT_ALEMBIC_DATABASE_URL` is set *out of band*, for example through
   a protected process environment or secret manager. Do not include DB
   credentials in shell arguments, PR comments or logs. Run the standard
   `alembic upgrade head` as part of normal **separately approved**
   maintenance. Revision `31t_direct_terminal_permission` creates
   `terminal:execute` idempotently while **granting it to no role**.
2. Identify an existing role ID intentionally trusted to execute host shell
   commands. Review its role name, current memberships and
   `role_permissions` in the target DB. **All principals whose JWT/API-key
   resolves to that role name will gain execution access** once explicitly
   granted: current `PermissionHelper` resolves by role name. If multiple
   role rows share that name, the helper **refuses** to grant; resolve identity
   ambiguity separately, rather than permitting cross-organization leakage.
3. Read-only preflight (no grant):
   ```bash
   python -m se.src.infrastructure.storage.terminal_permission_provisioning \
     --role-id "<EXACT_APPROVED_ROLE_ID>"
   ```
   Expect `PENDING_EXPLICIT_APPROVAL` (or `ALREADY_GRANTED`). A missing
   permission row means the database upgrade has not been applied.
4. After **explicit authorization for this exact role ID**, apply once:
   ```bash
   python -m se.src.infrastructure.storage.terminal_permission_provisioning \
     --role-id "<EXACT_APPROVED_ROLE_ID>" \
     --apply --confirm-role-id "<EXACT_APPROVED_ROLE_ID>"
   ```
   This is an idempotent, single-transaction insert into
   `role_permissions` and never modifies unrelated roles.
5. Verify that a newly authenticated signed JWT/API-key for an authorized
   role has `terminal:execute`, while ordinary members and write-only
   principals do not. Check the physical no-Popen negative cases and the
   actual authorization positive cases. Do not deploy without separate
   Owner authorization and the relevant security review.

**Default for installed users:** no role is automatically granted
`terminal:execute`. Existing admin principals without an explicit grant
remain unable to invoke terminal tools (fail-closed). This is an intentional
least-privilege change; the runbook provides a concrete upgrade path, not
transparent retroactive permission for every admin. If a production rollout
requires zero functional interruption for selected operators, execute the
**approved** role grants as an explicit preflight before exposing the new
authorization gate; do not weaken the security check to permit all admins.

## Rollback and concurrency considerations

A downgrade deletes only the permission row created by this revision when no
role references it. An explicitly granted permission remains in place on
downgrade to avoid silently revoking user-approved security state; remove
grants separately through reviewed administrative operations. Running the
provisioning command a second time reports `ALREADY_GRANTED`. Concurrent
permission changes are subject to the DB's unique role-permission constraint;
database errors must fail the operation rather than fabricate success.

## Limits

This P1 remediation does **not** certify in-process forged Identity provenance,
alternate aliases for the same physical host-command handler, MCP alias
collisions, R7/R12 nested Agent-origin, remote Client dispatch or OS-native
sandbox isolation. Preserve all earlier technical HOLDs for those workstreams.
PR #559 remains an audit-only branch; PR #562 remains unmerged until an
explicit new final-HEAD Owner merge decision.
