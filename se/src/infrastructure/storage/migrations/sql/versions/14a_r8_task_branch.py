"""Add normalized R8 TaskBranch persistence.

Revision ID: 14a_r8_task_branch
Revises: 13b_r7_pending_snapshot
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "14a_r8_task_branch"
down_revision: Union[str, None] = "13b_r7_pending_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SYNTHESIZED_REASON = "R8_MIGRATION_SYNTHESIZED_ROOT"
_NORMALIZED_REASON = "R8_MIGRATION_NORMALIZED_EXISTING_BRANCH"


def _fail(message: str) -> None:
    raise RuntimeError(f"R8_BRANCH_BACKFILL_CONFLICT: {message}")


def _legacy_branch_id(task_id: str) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:40]
    return f"r8_legacy_{digest}"


def _scalar_rows(bind, statement: str, params: dict | None = None) -> list:
    return list(bind.execute(sa.text(statement), params or {}).scalars().all())


def _mapping_rows(bind, statement: str, params: dict | None = None) -> list[dict]:
    return [
        dict(row)
        for row in bind.execute(sa.text(statement), params or {}).mappings().all()
    ]


def _preflight(bind) -> None:
    taskless = _scalar_rows(
        bind,
        """
        SELECT id
        FROM agent_executions
        WHERE task_id IS NULL
          AND branch_id IS NOT NULL
        ORDER BY id
        """,
    )
    if taskless:
        _fail(
            "non-task executions already carry branch_id: "
            + ", ".join(str(value) for value in taskless[:10])
        )

    cross_task = _mapping_rows(
        bind,
        """
        SELECT branch_id, COUNT(DISTINCT task_id) AS task_count
        FROM agent_executions
        WHERE branch_id IS NOT NULL
        GROUP BY branch_id
        HAVING COUNT(DISTINCT task_id) > 1
        ORDER BY branch_id
        """,
    )
    if cross_task:
        _fail(
            "branch_id reused across Tasks: "
            + ", ".join(str(row["branch_id"]) for row in cross_task[:10])
        )

    multi_branch_tasks = _mapping_rows(
        bind,
        """
        SELECT task_id, COUNT(DISTINCT branch_id) AS branch_count
        FROM agent_executions
        WHERE task_id IS NOT NULL
          AND branch_id IS NOT NULL
        GROUP BY task_id
        HAVING COUNT(DISTINCT branch_id) > 1
        ORDER BY task_id
        """,
    )
    if multi_branch_tasks:
        _fail(
            "Task has multiple pre-R8 branch identifiers: "
            + ", ".join(str(row["task_id"]) for row in multi_branch_tasks[:10])
        )

    checkpoint_mismatches = _mapping_rows(
        bind,
        """
        SELECT
            c.checkpoint_id,
            c.execution_id,
            c.task_id AS checkpoint_task_id,
            e.task_id AS execution_task_id,
            c.branch_id AS checkpoint_branch_id,
            e.branch_id AS execution_branch_id
        FROM agent_execution_checkpoints AS c
        JOIN agent_executions AS e
          ON e.id = c.execution_id
        WHERE e.task_id IS NOT NULL
          AND (
                c.task_id IS NULL
             OR c.task_id <> e.task_id
             OR (c.branch_id IS NULL AND e.branch_id IS NOT NULL)
             OR (c.branch_id IS NOT NULL AND e.branch_id IS NULL)
             OR (
                    c.branch_id IS NOT NULL
                AND e.branch_id IS NOT NULL
                AND c.branch_id <> e.branch_id
             )
          )
        ORDER BY c.checkpoint_id
        """,
    )
    if checkpoint_mismatches:
        _fail(
            "checkpoint lineage differs from AgentExecution before R8: "
            + ", ".join(
                str(row["checkpoint_id"])
                for row in checkpoint_mismatches[:10]
            )
        )


def _create_tables() -> None:
    op.create_table(
        "agent_task_branches",
        sa.Column("branch_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("parent_branch_id", sa.String(length=255), nullable=True),
        sa.Column("base_execution_id", sa.String(length=255), nullable=True),
        sa.Column("base_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("current_execution_id", sa.String(length=255), nullable=True),
        sa.Column(
            "resolution_state",
            sa.String(length=16),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_agent_task_branches_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "resolution_state IN "
            "('OPEN', 'ADOPTED', 'SUPERSEDED', 'DISCARDED', 'CANCELLED')",
            name="ck_agent_task_branches_resolution_state",
        ),
        sa.CheckConstraint(
            "parent_branch_id IS NULL OR parent_branch_id <> branch_id",
            name="ck_agent_task_branches_parent_not_self",
        ),
        sa.CheckConstraint(
            "("
            "parent_branch_id IS NULL "
            "AND base_execution_id IS NULL "
            "AND base_checkpoint_id IS NULL"
            ") OR ("
            "parent_branch_id IS NOT NULL "
            "AND base_execution_id IS NOT NULL "
            "AND base_checkpoint_id IS NOT NULL"
            ")",
            name="ck_agent_task_branches_origin_shape",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_branch_id"],
            ["agent_task_branches.branch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_execution_id"],
            ["agent_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_checkpoint_id"],
            ["agent_execution_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_execution_id"],
            ["agent_executions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )
    op.create_index(
        "ix_agent_task_branches_task_resolution",
        "agent_task_branches",
        ["task_id", "resolution_state"],
    )
    op.create_index(
        "ix_agent_task_branches_parent_branch_id",
        "agent_task_branches",
        ["parent_branch_id"],
    )
    op.create_index(
        "ix_agent_task_branches_current_execution_id",
        "agent_task_branches",
        ["current_execution_id"],
    )
    op.create_index(
        "ix_agent_task_branches_base_checkpoint_id",
        "agent_task_branches",
        ["base_checkpoint_id"],
    )

    op.create_table(
        "agent_task_branch_contexts",
        sa.Column("branch_id", sa.String(length=255), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "overlay_messages",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_agent_task_branch_contexts_revision_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["agent_task_branches.branch_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )


def _backfill(bind) -> None:
    tasks = _mapping_rows(
        bind,
        """
        SELECT t.id, t.created_by, t.status
        FROM agent_tasks AS t
        WHERE EXISTS (
            SELECT 1
            FROM agent_executions AS e
            WHERE e.task_id = t.id
        )
        ORDER BY t.id
        """,
    )

    for task in tasks:
        task_id = str(task["id"])
        existing_ids = [
            str(value)
            for value in _scalar_rows(
                bind,
                """
                SELECT DISTINCT branch_id
                FROM agent_executions
                WHERE task_id = :task_id
                  AND branch_id IS NOT NULL
                ORDER BY branch_id
                """,
                {"task_id": task_id},
            )
        ]
        if len(existing_ids) > 1:
            _fail(f"Task {task_id} has multiple branch identifiers")

        if existing_ids:
            branch_id = existing_ids[0]
            reason = _NORMALIZED_REASON
        else:
            branch_id = _legacy_branch_id(task_id)
            reason = _SYNTHESIZED_REASON

        resolution_state = (
            "CANCELLED" if str(task["status"]) == "CANCELLED" else "OPEN"
        )

        bind.execute(
            sa.text(
                """
                INSERT INTO agent_task_branches (
                    branch_id,
                    task_id,
                    parent_branch_id,
                    base_execution_id,
                    base_checkpoint_id,
                    current_execution_id,
                    resolution_state,
                    revision,
                    created_by,
                    reason
                )
                VALUES (
                    :branch_id,
                    :task_id,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    :resolution_state,
                    0,
                    :created_by,
                    :reason
                )
                """
            ),
            {
                "branch_id": branch_id,
                "task_id": task_id,
                "resolution_state": resolution_state,
                "created_by": str(task["created_by"]),
                "reason": reason,
            },
        )
        branch_contexts = sa.table(
            "agent_task_branch_contexts",
            sa.column("branch_id", sa.String(length=255)),
            sa.column("revision", sa.Integer()),
            sa.column("overlay_messages", sa.JSON()),
        )
        bind.execute(
            branch_contexts.insert().values(
                branch_id=branch_id,
                revision=0,
                overlay_messages=[],
            )
        )

        bind.execute(
            sa.text(
                """
                UPDATE agent_executions
                SET branch_id = :branch_id
                WHERE task_id = :task_id
                  AND branch_id IS NULL
                """
            ),
            {"branch_id": branch_id, "task_id": task_id},
        )
        bind.execute(
            sa.text(
                """
                UPDATE agent_execution_checkpoints
                SET branch_id = :branch_id
                WHERE branch_id IS NULL
                  AND execution_id IN (
                      SELECT id
                      FROM agent_executions
                      WHERE task_id = :task_id
                        AND branch_id = :branch_id
                  )
                """
            ),
            {"branch_id": branch_id, "task_id": task_id},
        )

        root_executions = _scalar_rows(
            bind,
            """
            SELECT id
            FROM agent_executions
            WHERE task_id = :task_id
              AND branch_id = :branch_id
              AND parent_execution_id IS NULL
            ORDER BY created_at, id
            """,
            {"task_id": task_id, "branch_id": branch_id},
        )
        if len(root_executions) == 1:
            bind.execute(
                sa.text(
                    """
                    UPDATE agent_task_branches
                    SET current_execution_id = :execution_id
                    WHERE branch_id = :branch_id
                    """
                ),
                {
                    "execution_id": str(root_executions[0]),
                    "branch_id": branch_id,
                },
            )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)
    _create_tables()
    _backfill(bind)


def downgrade() -> None:
    bind = op.get_bind()
    synthesized = _mapping_rows(
        bind,
        """
        SELECT branch_id, task_id
        FROM agent_task_branches
        WHERE reason = :reason
        ORDER BY branch_id
        """,
        {"reason": _SYNTHESIZED_REASON},
    )
    for branch in synthesized:
        branch_id = str(branch["branch_id"])
        task_id = str(branch["task_id"])
        bind.execute(
            sa.text(
                """
                UPDATE agent_execution_checkpoints
                SET branch_id = NULL
                WHERE branch_id = :branch_id
                  AND execution_id IN (
                      SELECT id
                      FROM agent_executions
                      WHERE task_id = :task_id
                        AND branch_id = :branch_id
                  )
                """
            ),
            {"branch_id": branch_id, "task_id": task_id},
        )
        bind.execute(
            sa.text(
                """
                UPDATE agent_executions
                SET branch_id = NULL
                WHERE task_id = :task_id
                  AND branch_id = :branch_id
                """
            ),
            {"branch_id": branch_id, "task_id": task_id},
        )

    op.drop_table("agent_task_branch_contexts")
    op.drop_index(
        "ix_agent_task_branches_base_checkpoint_id",
        table_name="agent_task_branches",
    )
    op.drop_index(
        "ix_agent_task_branches_current_execution_id",
        table_name="agent_task_branches",
    )
    op.drop_index(
        "ix_agent_task_branches_parent_branch_id",
        table_name="agent_task_branches",
    )
    op.drop_index(
        "ix_agent_task_branches_task_resolution",
        table_name="agent_task_branches",
    )
    op.drop_table("agent_task_branches")
