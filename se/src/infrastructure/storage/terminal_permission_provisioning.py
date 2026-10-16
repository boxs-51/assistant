"""Operator-approved terminal permission grant by exact persisted role ID.

Run the permission migration FIRST. By default this command is read-only.
No role gains terminal:execute just because it has admin:write or is named admin.
The grant is explicit, transaction-scoped, and idempotent. Database URL is
provided only through ASSISTANT_ALEMBIC_DATABASE_URL (never on argv or in logs).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine


PERMISSION = "terminal:execute"


class TerminalGrantError(ValueError):
    """A role/permission cannot be safely provisioned."""


def provision_terminal_execute(
    connection: Connection,
    *,
    role_id: str,
    apply: bool = False,
) -> dict[str, str]:
    """Preflight or atomically grant to exactly one existing persisted role.

    JWT/API-key permission loading currently resolves roles *by name*, not by
    organization-scoped role ID. Refuse duplicate names to avoid cross-role
    privilege bleed until that broader identity contract is remediated.
    """
    if not isinstance(role_id, str) or not role_id.strip() or role_id != role_id.strip():
        raise TerminalGrantError("A nonblank exact role ID is required")
    if type(apply) is not bool:
        raise TerminalGrantError("apply must be a boolean")

    row = connection.execute(
        sa.text("SELECT id, name FROM roles WHERE id = :role_id"),
        {"role_id": role_id},
    ).mappings().one_or_none()
    if row is None:
        raise TerminalGrantError("Requested role ID does not exist")
    name = row["name"]
    name_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM roles WHERE name = :name"),
        {"name": name},
    ).scalar_one()
    if name_count != 1:
        raise TerminalGrantError(
            "Role name is ambiguous across database roles; "
            "grant prohibited until role-name identity lookup is disambiguated"
        )

    permission_rows = connection.execute(
        sa.text("SELECT id FROM permissions WHERE name = :permission"),
        {"permission": PERMISSION},
    ).scalars().all()
    if len(permission_rows) != 1:
        raise TerminalGrantError(
            "terminal:execute permission is missing or ambiguous; "
            "run alembic upgrade head before granting"
        )
    permission_id = permission_rows[0]
    already_granted = connection.execute(
        sa.text(
            "SELECT 1 FROM role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ),
        {"role_id": role_id, "permission_id": permission_id},
    ).first() is not None

    if already_granted:
        status = "ALREADY_GRANTED"
    elif not apply:
        status = "PENDING_EXPLICIT_APPROVAL"
    else:
        # Separate SQL-level no-op on replay; never mutate other role bindings.
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT :role_id, :permission_id WHERE NOT EXISTS ("
                "SELECT 1 FROM role_permissions "
                "WHERE role_id = :role_id AND permission_id = :permission_id)"
            ),
            {"role_id": role_id, "permission_id": permission_id},
        )
        status = "GRANTED"
    return {"status": status, "role_id": role_id, "role_name": name}


async def _run(database_url: str, role_id: str, apply: bool) -> dict[str, str]:
    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.begin() as async_connection:
            return await async_connection.run_sync(
                provision_terminal_execute, role_id=role_id, apply=apply
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preflight an explicit, exact-role terminal:execute grant"
    )
    parser.add_argument("--role-id", required=True)
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply only after independently approving the exact role ID",
    )
    parser.add_argument(
        "--confirm-role-id",
        help="Repeat the exact --role-id when using --apply",
    )
    args = parser.parse_args()
    if args.apply and args.confirm_role_id != args.role_id:
        parser.error("--apply requires --confirm-role-id equal to --role-id")
    if not args.apply and args.confirm_role_id:
        parser.error("--confirm-role-id may only accompany --apply")

    url = os.environ.get("ASSISTANT_ALEMBIC_DATABASE_URL", "")
    if not url:
        parser.error("ASSISTANT_ALEMBIC_DATABASE_URL must be set in the environment")
    try:
        outcome = asyncio.run(_run(url, args.role_id, args.apply))
    except (TerminalGrantError, sa.exc.SQLAlchemyError) as exc:
        parser.exit(1, f"Provisioning rejected: {type(exc).__name__}: {exc}\n")
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
