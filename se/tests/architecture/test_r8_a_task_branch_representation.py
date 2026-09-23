from __future__ import annotations

from se.src.domain.schemas.multi_agent import (
    BranchResolutionState,
    TaskBranch,
    TaskBranchContext,
)
from se.src.infrastructure.storage.models.sql.agent import (
    AgentTaskBranchContextRecord,
    AgentTaskBranchRecord,
)


def test_r8_a_domain_contract_is_exact_and_non_runtime():
    assert [item.value for item in BranchResolutionState] == [
        "OPEN",
        "ADOPTED",
        "SUPERSEDED",
        "DISCARDED",
        "CANCELLED",
    ]

    branch = TaskBranch(
        branch_id="branch-r8-a",
        task_id="task-r8-a",
        created_by="user-r8-a",
    )
    assert branch.resolution_state is BranchResolutionState.OPEN
    assert branch.revision == 0
    assert branch.parent_branch_id is None
    assert branch.base_execution_id is None
    assert branch.base_checkpoint_id is None
    assert branch.current_execution_id is None

    context = TaskBranchContext(branch_id=branch.branch_id)
    assert context.revision == 0
    assert context.overlay_messages == []


def test_r8_a_sql_schema_has_frozen_constraints_and_indexes():
    branch_table = AgentTaskBranchRecord.__table__
    context_table = AgentTaskBranchContextRecord.__table__

    branch_constraints = {
        constraint.name
        for constraint in branch_table.constraints
        if constraint.name
    }
    assert {
        "ck_agent_task_branches_revision_nonnegative",
        "ck_agent_task_branches_resolution_state",
        "ck_agent_task_branches_parent_not_self",
        "ck_agent_task_branches_origin_shape",
    } <= branch_constraints

    branch_indexes = {index.name for index in branch_table.indexes}
    assert {
        "ix_agent_task_branches_task_resolution",
        "ix_agent_task_branches_parent_branch_id",
        "ix_agent_task_branches_current_execution_id",
        "ix_agent_task_branches_base_checkpoint_id",
    } <= branch_indexes

    context_constraints = {
        constraint.name
        for constraint in context_table.constraints
        if constraint.name
    }
    assert "ck_agent_task_branch_contexts_revision_nonnegative" in context_constraints

    assert branch_table.c.branch_id.primary_key
    assert context_table.c.branch_id.primary_key
    assert not branch_table.c.task_id.nullable
    assert not branch_table.c.resolution_state.nullable
    assert not context_table.c.overlay_messages.nullable
