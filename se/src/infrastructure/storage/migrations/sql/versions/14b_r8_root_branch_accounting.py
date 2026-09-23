"""Reconcile R8 root TaskBranch accounting with TaskBudget.

Revision ID: 14b_r8_root_branch_accounting
Revises: 14a_r8_task_branch
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "14b_r8_root_branch_accounting"
down_revision: Union[str, None] = "14a_r8_task_branch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fail(message: str) -> None:
    raise RuntimeError(f"R8_ROOT_BRANCH_ACCOUNTING_CONFLICT: {message}")


def _branch_fingerprint(branch_id: str) -> str:
    encoded = json.dumps(
        {
            "kind": "BRANCH",
            "reservation_key": branch_id,
            "payload": {},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping_rows(bind, statement: str, params: dict | None = None) -> list[dict]:
    return [
        dict(row)
        for row in bind.execute(sa.text(statement), params or {}).mappings().all()
    ]


def _branch_reservations(bind, task_id: str) -> list[dict]:
    return _mapping_rows(
        bind,
        """
        SELECT reservation_key, payload_fingerprint
        FROM agent_task_budget_reservations
        WHERE task_id = :task_id
          AND kind = 'BRANCH'
        ORDER BY reservation_key
        """,
        {"task_id": task_id},
    )


def _branches(bind, task_id: str) -> list[dict]:
    return _mapping_rows(
        bind,
        """
        SELECT branch_id, resolution_state
        FROM agent_task_branches
        WHERE task_id = :task_id
        ORDER BY branch_id
        """,
        {"task_id": task_id},
    )


def _validate_matching_reservation(
    *,
    task_id: str,
    branch_id: str,
    reservations: list[dict],
) -> None:
    expected = _branch_fingerprint(branch_id)
    if len(reservations) != 1:
        _fail(
            f"Task {task_id} expected exactly one BRANCH reservation "
            f"for {branch_id}, found {len(reservations)}"
        )
    reservation = reservations[0]
    if str(reservation["reservation_key"]) != branch_id:
        _fail(
            f"Task {task_id} BRANCH reservation key does not match "
            f"normalized branch {branch_id}"
        )
    if str(reservation["payload_fingerprint"]) != expected:
        _fail(
            f"Task {task_id} BRANCH reservation fingerprint does not "
            f"match canonical empty payload"
        )


def _preflight(bind) -> None:
    multi = _mapping_rows(
        bind,
        """
        SELECT task_id, COUNT(*) AS branch_count
        FROM agent_task_branches
        GROUP BY task_id
        HAVING COUNT(*) > 1
        ORDER BY task_id
        """,
    )
    if multi:
        _fail(
            "R8-B cannot reconcile a Task that already has multiple "
            "TaskBranch rows: "
            + ", ".join(str(row["task_id"]) for row in multi[:10])
        )

    orphan_branch_reservations = _mapping_rows(
        bind,
        """
        SELECT r.task_id, r.reservation_key
        FROM agent_task_budget_reservations AS r
        LEFT JOIN agent_task_branches AS b
          ON b.task_id = r.task_id
         AND b.branch_id = r.reservation_key
        WHERE r.kind = 'BRANCH'
          AND b.branch_id IS NULL
        ORDER BY r.task_id, r.reservation_key
        """,
    )
    if orphan_branch_reservations:
        _fail(
            "BRANCH reservation exists without matching normalized branch: "
            + ", ".join(
                f"{row['task_id']}:{row['reservation_key']}"
                for row in orphan_branch_reservations[:10]
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)

    budgets = _mapping_rows(
        bind,
        """
        SELECT task_id, revision, active_branches
        FROM agent_task_budgets
        ORDER BY task_id
        """,
    )

    for budget in budgets:
        task_id = str(budget["task_id"])
        revision_value = int(budget["revision"])
        active_branches = int(budget["active_branches"])
        branches = _branches(bind, task_id)
        reservations = _branch_reservations(bind, task_id)

        if not branches:
            if active_branches != 0 or reservations:
                _fail(
                    f"Task {task_id} has no normalized branch but "
                    f"active_branches={active_branches} and "
                    f"{len(reservations)} BRANCH reservations"
                )
            continue

        branch = branches[0]
        branch_id = str(branch["branch_id"])
        state = str(branch["resolution_state"])

        if state != "OPEN":
            if active_branches != 0 or reservations:
                _fail(
                    f"Task {task_id} has non-OPEN branch {branch_id} but "
                    f"active_branches={active_branches} and "
                    f"{len(reservations)} BRANCH reservations"
                )
            continue

        if active_branches == 0 and not reservations:
            updated = bind.execute(
                sa.text(
                    """
                    UPDATE agent_task_budgets
                    SET active_branches = 1,
                        revision = revision + 1
                    WHERE task_id = :task_id
                      AND revision = :revision
                      AND active_branches = 0
                    """
                ),
                {"task_id": task_id, "revision": revision_value},
            )
            if updated.rowcount != 1:
                _fail(
                    f"TaskBudget changed during R8-B migration for {task_id}"
                )
            bind.execute(
                sa.text(
                    """
                    INSERT INTO agent_task_budget_reservations (
                        task_id,
                        kind,
                        reservation_key,
                        payload_fingerprint
                    )
                    VALUES (
                        :task_id,
                        'BRANCH',
                        :reservation_key,
                        :payload_fingerprint
                    )
                    """
                ),
                {
                    "task_id": task_id,
                    "reservation_key": branch_id,
                    "payload_fingerprint": _branch_fingerprint(branch_id),
                },
            )
            continue

        if active_branches == 1:
            _validate_matching_reservation(
                task_id=task_id,
                branch_id=branch_id,
                reservations=reservations,
            )
            continue

        _fail(
            f"Task {task_id} OPEN branch accounting is inconsistent: "
            f"active_branches={active_branches}, "
            f"reservations={len(reservations)}"
        )

    budgetless = _mapping_rows(
        bind,
        """
        SELECT b.task_id, b.branch_id
        FROM agent_task_branches AS b
        LEFT JOIN agent_task_budgets AS tb
          ON tb.task_id = b.task_id
        WHERE tb.task_id IS NULL
        ORDER BY b.task_id
        """,
    )
    for branch in budgetless:
        reservations = _branch_reservations(bind, str(branch["task_id"]))
        if reservations:
            _fail(
                f"Budgetless Task {branch['task_id']} already has BRANCH "
                "reservation state that cannot be reconstructed safely"
            )


def downgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)

    budgets = _mapping_rows(
        bind,
        """
        SELECT task_id, revision, active_branches
        FROM agent_task_budgets
        ORDER BY task_id
        """,
    )

    for budget in budgets:
        task_id = str(budget["task_id"])
        revision_value = int(budget["revision"])
        active_branches = int(budget["active_branches"])
        branches = _branches(bind, task_id)
        reservations = _branch_reservations(bind, task_id)

        if not branches:
            if active_branches != 0 or reservations:
                _fail(
                    f"Task {task_id} cannot downgrade inconsistent "
                    "branch accounting"
                )
            continue

        branch = branches[0]
        branch_id = str(branch["branch_id"])
        state = str(branch["resolution_state"])

        if state != "OPEN":
            if active_branches != 0 or reservations:
                _fail(
                    f"Task {task_id} non-OPEN branch has accounting that "
                    "cannot be downgraded safely"
                )
            continue

        if active_branches != 1:
            _fail(
                f"Task {task_id} OPEN branch requires active_branches=1 "
                "before R8-B downgrade"
            )
        _validate_matching_reservation(
            task_id=task_id,
            branch_id=branch_id,
            reservations=reservations,
        )
        bind.execute(
            sa.text(
                """
                DELETE FROM agent_task_budget_reservations
                WHERE task_id = :task_id
                  AND kind = 'BRANCH'
                  AND reservation_key = :branch_id
                """
            ),
            {"task_id": task_id, "branch_id": branch_id},
        )
        updated = bind.execute(
            sa.text(
                """
                UPDATE agent_task_budgets
                SET active_branches = 0,
                    revision = revision + 1
                WHERE task_id = :task_id
                  AND revision = :revision
                  AND active_branches = 1
                """
            ),
            {"task_id": task_id, "revision": revision_value},
        )
        if updated.rowcount != 1:
            _fail(
                f"TaskBudget changed during R8-B downgrade for {task_id}"
            )
