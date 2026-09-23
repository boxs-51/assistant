from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[3]


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _upgrade_to_14a(database: Path, monkeypatch) -> Config:
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", url)
    config = _config(database)
    command.upgrade(config, "14a_r8_task_branch")
    return config


def _fingerprint(branch_id: str) -> str:
    payload = {
        "kind": "BRANCH",
        "reservation_key": branch_id,
        "payload": {},
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _task(connection, task_id: str):
    connection.execute(
        """
        INSERT INTO agent_tasks (
            id, session_id, created_by, assigned_agent_id,
            revision, status, wait_reasons, input
        )
        VALUES (?, ?, ?, ?, 0, 'RUNNING', '[]', '{}')
        """,
        (
            task_id,
            f"session-{task_id}",
            f"user-{task_id}",
            "agent-r8-b",
        ),
    )


def _budget(connection, task_id: str, *, active_branches: int = 0):
    connection.execute(
        """
        INSERT INTO agent_task_budgets (
            task_id, revision, state,
            max_total_executions, max_active_executions,
            max_active_branches, max_parallel_agents,
            max_total_tool_calls, max_total_inference_calls,
            max_total_tokens, max_total_cost_usd,
            max_delegation_depth,
            policy_version, policy_fingerprint,
            deny_recursive_agent_cycle,
            used_executions, active_executions,
            active_branches, active_parallel_agents,
            used_tool_calls, used_inference_calls,
            used_tokens, used_cost_usd,
            closed_at
        )
        VALUES (
            ?, 0, 'OPEN',
            8, 4,
            4, 4,
            32, 32,
            10000, 10,
            4,
            'r8-b-test', ?,
            1,
            0, 0,
            ?, 0,
            0, 0,
            0, 0,
            NULL
        )
        """,
        (task_id, "a" * 64, active_branches),
    )


def _branch(
    connection,
    task_id: str,
    branch_id: str,
    *,
    state: str = "OPEN",
):
    connection.execute(
        """
        INSERT INTO agent_task_branches (
            branch_id, task_id,
            parent_branch_id, base_execution_id, base_checkpoint_id,
            current_execution_id,
            resolution_state, revision,
            created_by, reason
        )
        VALUES (?, ?, NULL, NULL, NULL, NULL, ?, 0, ?, ?)
        """,
        (
            branch_id,
            task_id,
            state,
            f"user-{task_id}",
            "R8_MIGRATION_SYNTHESIZED_ROOT",
        ),
    )
    connection.execute(
        """
        INSERT INTO agent_task_branch_contexts (
            branch_id, revision, overlay_messages
        )
        VALUES (?, 0, '[]')
        """,
        (branch_id,),
    )


def _reservation(connection, task_id: str, branch_id: str, fingerprint: str):
    connection.execute(
        """
        INSERT INTO agent_task_budget_reservations (
            task_id, kind, reservation_key, payload_fingerprint
        )
        VALUES (?, 'BRANCH', ?, ?)
        """,
        (task_id, branch_id, fingerprint),
    )


def test_r8_b_remains_on_single_linear_migration_chain(tmp_path: Path):
    config = _config(tmp_path / "unused.sqlite")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["15a_r9_retry_admission"]
    assert (
        script.get_revision("15a_r9_retry_admission").down_revision
        == "14d_r8_fork_runtime_seed"
    )
    assert script.get_revision("14b_r8_root_branch_accounting") is not None
    assert (
        script.get_revision("14c_r8_fork_admission").down_revision
        == "14b_r8_root_branch_accounting"
    )
    assert (
        script.get_revision("14d_r8_fork_runtime_seed").down_revision
        == "14c_r8_fork_admission"
    )


def test_r8_b_reconciles_historical_root_branch_accounting_and_downgrades(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "r8_b_migration.sqlite"
    config = _upgrade_to_14a(database, monkeypatch)

    connection = sqlite3.connect(database)
    try:
        # HB-A: no branch, pristine branch counter.
        _task(connection, "task-none")
        _budget(connection, "task-none", active_branches=0)

        # HB-B: OPEN branch exists but was intentionally not accounted by R8-A.
        _task(connection, "task-open")
        _budget(connection, "task-open", active_branches=0)
        _branch(connection, "task-open", "branch-open")

        # HB-C: already-correct durable accounting is accepted unchanged.
        _task(connection, "task-accounted")
        _budget(connection, "task-accounted", active_branches=1)
        _branch(connection, "task-accounted", "branch-accounted")
        _reservation(
            connection,
            "task-accounted",
            "branch-accounted",
            _fingerprint("branch-accounted"),
        )

        # HB-D: non-OPEN historical branch consumes no live branch slot.
        _task(connection, "task-cancelled")
        _budget(connection, "task-cancelled", active_branches=0)
        _branch(
            connection,
            "task-cancelled",
            "branch-cancelled",
            state="CANCELLED",
        )

        # HB-E: branch history without reconstructable TaskBudget is quarantined.
        _task(connection, "task-budgetless")
        _branch(connection, "task-budgetless", "branch-budgetless")
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            """
            SELECT active_branches
            FROM agent_task_budgets
            WHERE task_id = 'task-none'
            """
        ).fetchone()[0] == 0

        open_row = connection.execute(
            """
            SELECT revision, active_branches
            FROM agent_task_budgets
            WHERE task_id = 'task-open'
            """
        ).fetchone()
        assert open_row == (1, 1)

        reservation = connection.execute(
            """
            SELECT reservation_key, payload_fingerprint
            FROM agent_task_budget_reservations
            WHERE task_id = 'task-open'
              AND kind = 'BRANCH'
            """
        ).fetchone()
        assert reservation == (
            "branch-open",
            _fingerprint("branch-open"),
        )

        # Already-correct accounting is not charged again.
        accounted = connection.execute(
            """
            SELECT revision, active_branches
            FROM agent_task_budgets
            WHERE task_id = 'task-accounted'
            """
        ).fetchone()
        assert accounted == (0, 1)

        assert connection.execute(
            """
            SELECT active_branches
            FROM agent_task_budgets
            WHERE task_id = 'task-cancelled'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_task_budget_reservations
            WHERE task_id = 'task-cancelled'
              AND kind = 'BRANCH'
            """
        ).fetchone()[0] == 0

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM agent_task_budgets
            WHERE task_id = 'task-budgetless'
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()

    command.downgrade(config, "14a_r8_task_branch")

    connection = sqlite3.connect(database)
    try:
        # 14a intentionally had no root-branch accounting authority.
        for task_id in ("task-open", "task-accounted"):
            assert connection.execute(
                """
                SELECT active_branches
                FROM agent_task_budgets
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM agent_task_budget_reservations
                WHERE task_id = ?
                  AND kind = 'BRANCH'
                """,
                (task_id,),
            ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("active_branches", "with_reservation", "wrong_fingerprint"),
    [
        (1, False, False),
        (0, True, False),
        (1, True, True),
    ],
)
def test_r8_b_migration_fails_closed_on_counter_ledger_disagreement(
    tmp_path: Path,
    monkeypatch,
    active_branches: int,
    with_reservation: bool,
    wrong_fingerprint: bool,
):
    database = tmp_path / (
        f"r8_b_conflict_{active_branches}_{with_reservation}_"
        f"{wrong_fingerprint}.sqlite"
    )
    config = _upgrade_to_14a(database, monkeypatch)

    connection = sqlite3.connect(database)
    try:
        _task(connection, "task-conflict")
        _budget(
            connection,
            "task-conflict",
            active_branches=active_branches,
        )
        _branch(connection, "task-conflict", "branch-conflict")
        if with_reservation:
            _reservation(
                connection,
                "task-conflict",
                "branch-conflict",
                (
                    "0" * 64
                    if wrong_fingerprint
                    else _fingerprint("branch-conflict")
                ),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError,
        match="R8_ROOT_BRANCH_ACCOUNTING_CONFLICT",
    ):
        command.upgrade(config, "head")
