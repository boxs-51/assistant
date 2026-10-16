"""P1: upgrade-safe persisted terminal permission, no implicit admin grants.

Tests use actual Alembic upgrade/downgrade and actual SQL role-permission
lookup, not an Identity permissions override. The existing Direct real-signed
JWT no-Popen/positive tests remain unchanged in their original file.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from se.src.application.policy.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.terminal_permission_provisioning import (
    TerminalGrantError,
    main as provision_main,
    provision_terminal_execute,
)
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.transport.gateway.authentication.authenticators.jwt_authenticator import (
    JWTAuthenticator,
)


ROOT = Path(__file__).resolve().parents[3]
PREVIOUS = "30a_ctx_f5_user_wide_memory_scope"
REVISION = "31t_direct_terminal_permission"


def _config(database: Path, monkeypatch) -> Config:
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    return config


def _seed_legacy_rows(database: Path) -> None:
    # Pre-existing installation, not a new test fixture provisioning the new
    # permission. Only admin:write is present; no terminal grant.
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO users (id,email,password_hash,status) VALUES (?,?,?,?)",
            ("p1-admin", "p1-admin@example.invalid", "not-a-password", "active"),
        )
        db.executemany(
            "INSERT INTO roles (id,name,organization_id) VALUES (?,?,NULL)",
            [("p1-admin-role", "admin"), ("p1-member-role", "member")],
        )
        db.execute(
            "INSERT INTO permissions (id,name,description) VALUES (?,?,?)",
            ("p1-write-permission", "admin:write", "Legacy admin permission"),
        )
        db.execute(
            "INSERT INTO role_permissions (role_id,permission_id) VALUES (?,?)",
            ("p1-admin-role", "p1-write-permission"),
        )
        db.execute(
            "INSERT INTO organizations (id,owner_id,name) VALUES (?,?,?)",
            ("p1-existing-org", "p1-admin", "Existing organization"),
        )
        db.execute(
            "INSERT INTO members (organization_id,user_id) VALUES (?,?)",
            ("p1-existing-org", "p1-admin"),
        )
        db.execute(
            "INSERT INTO member_roles "
            "(member_organization_id,member_user_id,role_id) VALUES (?,?,?)",
            ("p1-existing-org", "p1-admin", "p1-admin-role"),
        )


def _terminal_count(database: Path) -> tuple[int, int]:
    with sqlite3.connect(database) as db:
        permission_count = db.execute(
            "SELECT COUNT(*) FROM permissions WHERE name='terminal:execute'"
        ).fetchone()[0]
        grant_count = db.execute(
            "SELECT COUNT(*) FROM role_permissions rp "
            "JOIN permissions p ON p.id=rp.permission_id "
            "WHERE p.name='terminal:execute'"
        ).fetchone()[0]
        return (permission_count, grant_count)


def test_p1_real_upgrade_replay_and_downgrade_does_not_revoke_grants(
    tmp_path, monkeypatch,
):
    database = tmp_path / "p1-upgrade.sqlite3"
    config = _config(database, monkeypatch)
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    command.upgrade(config, PREVIOUS)
    _seed_legacy_rows(database)

    assert _terminal_count(database) == (0, 0)
    command.upgrade(config, REVISION)
    # Creating the permission must NOT equate admin:write with execution.
    assert _terminal_count(database) == (1, 0)
    with sa.create_engine(f"sqlite:///{database.as_posix()}").begin() as conn:
        assert provision_terminal_execute(
            conn, role_id="p1-admin-role", apply=False
        )["status"] == "PENDING_EXPLICIT_APPROVAL"
    assert _terminal_count(database) == (1, 0)

    # A downgrade before any grant may safely remove only the row we inserted.
    command.downgrade(config, PREVIOUS)
    assert _terminal_count(database) == (0, 0)
    command.upgrade(config, REVISION)
    assert _terminal_count(database) == (1, 0)

    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as conn:
            assert provision_terminal_execute(
                conn, role_id="p1-admin-role", apply=True
            )["status"] == "GRANTED"
            assert provision_terminal_execute(
                conn, role_id="p1-admin-role", apply=True
            )["status"] == "ALREADY_GRANTED"
        assert _terminal_count(database) == (1, 1)
        # Downgrade must preserve role-linked explicit grants.
        command.downgrade(config, PREVIOUS)
        assert _terminal_count(database) == (1, 1)
        command.upgrade(config, REVISION)
        assert _terminal_count(database) == (1, 1)
    finally:
        engine.dispose()


def test_p1_explicit_grant_rejects_missing_and_ambiguous_roles(
    tmp_path, monkeypatch,
):
    db = tmp_path / "p1-reject.sqlite3"
    config = _config(db, monkeypatch)
    command.upgrade(config, REVISION)
    engine = sa.create_engine(f"sqlite:///{db.as_posix()}")
    try:
        with engine.begin() as conn:
            with pytest.raises(TerminalGrantError, match="does not exist"):
                provision_terminal_execute(conn, role_id="missing", apply=True)
            with pytest.raises(TerminalGrantError, match="nonblank"):
                provision_terminal_execute(conn, role_id=" ", apply=True)
            conn.execute(
                sa.text("INSERT INTO roles (id,name) VALUES (:id,:name)"),
                {"id": "p1-role-1", "name": "admin"},
            )
            conn.execute(
                sa.text("INSERT INTO roles (id,name) VALUES (:id,:name)"),
                {"id": "p1-role-2", "name": "admin"},
            )
            with pytest.raises(TerminalGrantError, match="ambiguous"):
                provision_terminal_execute(conn, role_id="p1-role-1", apply=True)
        assert _terminal_count(db) == (1, 0)
    finally:
        engine.dispose()


def test_p1_operator_cli_requires_exact_role_confirmation(
    tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "p1-cli.sqlite3"
    config = _config(db, monkeypatch)
    command.upgrade(config, REVISION)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO roles (id,name) VALUES (?,?)",
            ("p1-exact-role", "terminal-operator"),
        )

    monkeypatch.setattr(
        sys, "argv", ["terminal_permission_provisioning", "--role-id", "p1-exact-role"],
    )
    provision_main()
    assert "PENDING_EXPLICIT_APPROVAL" in capsys.readouterr().out
    assert _terminal_count(db) == (1, 0)
    monkeypatch.setattr(
        sys, "argv",
        ["terminal_permission_provisioning", "--role-id", "p1-exact-role",
         "--apply", "--confirm-role-id", "wrong"],
    )
    with pytest.raises(SystemExit):
        provision_main()
    assert _terminal_count(db) == (1, 0)

    monkeypatch.setattr(
        sys, "argv",
        ["terminal_permission_provisioning", "--role-id", "p1-exact-role",
         "--apply", "--confirm-role-id", "p1-exact-role"],
    )
    provision_main()
    assert '"status": "GRANTED"' in capsys.readouterr().out
    assert _terminal_count(db) == (1, 1)


@pytest.mark.asyncio
async def test_p1_real_persisted_jwt_role_stays_denied_until_opt_in(
    tmp_path, monkeypatch,
):
    db = tmp_path / "p1-jwt.sqlite3"
    config = _config(db, monkeypatch)
    await asyncio.to_thread(command.upgrade, config, PREVIOUS)
    _seed_legacy_rows(db)
    await asyncio.to_thread(command.upgrade, config, REVISION)
    driver = SQLiteDriver(
        DriverConfig(enabled=True, required=True, options={"path": str(db)})
    )
    factory = lambda: SqlAlchemyUnitOfWork(driver)
    token_source = SimpleNamespace(
        verify_access_token=lambda _token: Identity(
            auth_type="jwt", user_id="p1-admin",
            organization_id="p1-existing-org", roles=["admin"]
        )
    )
    jwt = JWTAuthenticator(token_source, factory)
    cap = CapabilityDefinition(
        id="terminal.run", name="terminal.run",
        description="Terminal execution permission provisioning regression",
        input_schema={"type": "object"},
    )
    try:
        before = await jwt.authenticate("p1-fixture-claims")
        assert set(before.permissions) == {"admin:write"}
        assert (
            AuthorizationService().authorize(before, cap)
            is AuthorizationDecision.DENY
        )
        # The sole explicit administrative role grant is applied in a
        # separate connection; the next authentication must re-read SQL.
        engine = sa.create_engine(f"sqlite:///{db.as_posix()}")
        try:
            with engine.begin() as connection:
                assert provision_terminal_execute(
                    connection, role_id="p1-admin-role", apply=True
                )["status"] == "GRANTED"
        finally:
            engine.dispose()
        after = await jwt.authenticate("p1-fixture-claims")
        assert set(after.permissions) == {"admin:write", "terminal:execute"}
        assert (
            AuthorizationService().authorize(after, cap)
            is AuthorizationDecision.ALLOW
        )
        async with factory() as uow:
            member_rights, resolved_org = (
                await uow.permissions.get_permissions_for_member_role_ids(
                    user_id="p1-admin",
                    organization_id="p1-existing-org",
                    allowed_role_names={"member"},
                )
            )
            assert member_rights == set()
            assert resolved_org == "p1-existing-org"
    finally:
        await driver.disconnect()
