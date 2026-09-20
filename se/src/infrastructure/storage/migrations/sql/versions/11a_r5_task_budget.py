"""Add R5 durable TaskBudget representation and task revision CAS.

Revision ID: 11a_r5_task_budget
Revises: 10a_r4_active_budget_wait_ttl
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "11a_r5_task_budget"
down_revision: Union[str, None] = "10a_r4_active_budget_wait_ttl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_check_constraint(
            "ck_agent_tasks_revision_nonnegative",
            "revision >= 0",
        )

    op.create_table(
        "agent_task_budgets",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("max_total_executions", sa.Integer(), nullable=False),
        sa.Column("max_active_executions", sa.Integer(), nullable=False),
        sa.Column("max_active_branches", sa.Integer(), nullable=False),
        sa.Column("max_parallel_agents", sa.Integer(), nullable=False),
        sa.Column("max_total_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_total_inference_calls", sa.Integer(), nullable=False),
        sa.Column("max_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_total_cost_usd", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_delegation_depth", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "deny_recursive_agent_cycle",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("used_executions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_executions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_branches", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_parallel_agents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_tool_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_inference_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_cost_usd", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_task_budget_revision_nonnegative"),
        sa.CheckConstraint("state IN ('OPEN', 'CLOSED')", name="ck_task_budget_state"),
        sa.CheckConstraint(
            "max_total_executions > 0 "
            "AND max_active_executions > 0 "
            "AND max_active_branches > 0 "
            "AND max_parallel_agents > 0 "
            "AND max_total_tool_calls > 0 "
            "AND max_total_inference_calls > 0 "
            "AND max_delegation_depth > 0",
            name="ck_task_budget_required_limits_positive",
        ),
        sa.CheckConstraint(
            "max_total_tokens IS NULL OR max_total_tokens > 0",
            name="ck_task_budget_optional_tokens_positive",
        ),
        sa.CheckConstraint(
            "max_total_cost_usd IS NULL OR max_total_cost_usd > 0",
            name="ck_task_budget_optional_cost_positive",
        ),
        sa.CheckConstraint(
            "used_executions >= 0 "
            "AND active_executions >= 0 "
            "AND active_branches >= 0 "
            "AND active_parallel_agents >= 0 "
            "AND used_tool_calls >= 0 "
            "AND used_inference_calls >= 0 "
            "AND used_tokens >= 0 "
            "AND used_cost_usd >= 0",
            name="ck_task_budget_counters_nonnegative",
        ),
        sa.CheckConstraint(
            "active_executions <= used_executions "
            "AND used_executions <= max_total_executions "
            "AND active_executions <= max_active_executions",
            name="ck_task_budget_execution_bounds",
        ),
        sa.CheckConstraint(
            "active_parallel_agents <= active_executions "
            "AND active_parallel_agents <= max_parallel_agents",
            name="ck_task_budget_parallel_agent_bounds",
        ),
        sa.CheckConstraint(
            "active_branches <= max_active_branches",
            name="ck_task_budget_branch_bounds",
        ),
        sa.CheckConstraint(
            "used_tool_calls <= max_total_tool_calls",
            name="ck_task_budget_tool_bounds",
        ),
        sa.CheckConstraint(
            "used_inference_calls <= max_total_inference_calls",
            name="ck_task_budget_inference_bounds",
        ),
        sa.CheckConstraint(
            "(state = 'OPEN' AND closed_at IS NULL) "
            "OR (state = 'CLOSED' AND closed_at IS NOT NULL)",
            name="ck_task_budget_closed_at_state",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_agent_task_budgets_state",
        "agent_task_budgets",
        ["state"],
    )

    op.create_table(
        "agent_task_budget_reservations",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("reservation_key", sa.String(length=255), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ("
            "'NEW_EXECUTION', 'RESUME_EXECUTION', 'RELEASE_EXECUTION', "
            "'TOOL_CALL', 'INFERENCE', 'USAGE', "
            "'BRANCH', 'RELEASE_BRANCH'"
            ")",
            name="ck_task_budget_reservation_kind",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "kind", "reservation_key"),
    )


def downgrade() -> None:
    op.drop_table("agent_task_budget_reservations")
    op.drop_index(
        "ix_agent_task_budgets_state",
        table_name="agent_task_budgets",
    )
    op.drop_table("agent_task_budgets")
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_tasks_revision_nonnegative",
            type_="check",
        )
        batch_op.drop_column("revision")
