from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess
import sys

from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import TaskBudgetService
from se.src.runtimes.agent.waiting_checkpoint import (
    stage_waiting_checkpoint,
    validate_checkpoint_parent_lineage_in_uow,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/agent_execution_r11/"
    "R11_F1C_SEMANTIC_EDGE_SERIALIZATION_C50D0670.md"
)


def test_r11_f1c_child_task_creation_uses_shared_parent_gc_fence():
    source = inspect.getsource(TaskBudgetService.create_task_with_budget)

    assert 'normalized_task.get("parent_task_id")' in source
    assert "lock_task_gc_serialization_fence" in source
    assert "Parent AgentTask not found" in source
    assert source.index("lock_task_gc_serialization_fence") < source.index(
        "save_task(normalized_task)"
    )


def test_r11_f1c_task_gc_fence_is_real_on_sqlite_and_row_locking_dialects():
    source = inspect.getsource(AgentRepository.lock_task_gc_serialization_fence)

    assert 'dialect == "sqlite"' in source
    assert "update(AgentTaskRecord)" in source
    assert "revision=AgentTaskRecord.revision" in source
    assert "updated_at=AgentTaskRecord.updated_at" in source
    assert "get_task_for_update(task_id)" in source


def test_r11_f1c_invocation_gc_fence_is_real_and_r6_lifecycle_neutral():
    source = inspect.getsource(
        CapabilityInvocationRepository.lock_invocation_gc_serialization_fence
    )

    assert 'dialect == "sqlite"' in source
    assert "update(CapabilityInvocationRecord)" in source
    assert "revision=CapabilityInvocationRecord.revision" in source
    assert "updated_at=CapabilityInvocationRecord.updated_at" in source
    assert ".with_for_update()" in source


def test_r11_f1c_fresh_tool_call_rejects_existing_invocation_before_insert():
    source = inspect.getsource(DurableAgentStore.save_tool_call)

    assert "lock_invocation_gc_serialization_fence" in source
    assert "Fresh AgentToolCall invocation_id is already bound" in source
    assert source.index("lock_invocation_gc_serialization_fence") < source.index(
        "uow.agents.save_tool_call(values)"
    )


def test_r11_f1c_checkpoint_parent_lineage_is_proven_before_transcript_write():
    helper = inspect.getsource(validate_checkpoint_parent_lineage_in_uow)
    stage = inspect.getsource(stage_waiting_checkpoint)
    legacy = inspect.getsource(DurableAgentStore.materialize_legacy_checkpoint)

    for phrase in (
        "parent.execution_id != execution.id",
        "parent.task_id != execution.task_id",
        "parent.branch_id != execution.branch_id",
        "parent.execution_revision",
        "child_execution_revision",
    ):
        assert phrase in helper

    assert "validate_checkpoint_parent_lineage_in_uow" in stage
    assert stage.index("validate_checkpoint_parent_lineage_in_uow") < stage.index(
        "write_transcript_representation_in_uow"
    )

    assert "validate_checkpoint_parent_lineage_in_uow" in legacy
    assert legacy.index("validate_checkpoint_parent_lineage_in_uow") < legacy.index(
        "write_transcript_representation_in_uow"
    )
    assert "LEGACY_CHECKPOINT_UNSAFE" in legacy


def test_r11_f1c_semantic_edge_contract_freezes_exact_baseline_and_boundaries():
    text = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "4e1e5cb3a90412e74a82d6248e53e57dfae30a90",
        "CTX-F5-0 / PR #80 = MERGED",
        "Architecture #1220",
        "P1-R11-F1C-SEMANTIC-EDGE-RACE-1",
        "parent_task_id",
        "parent_checkpoint_id",
        "invocation_id",
        "lock_task_gc_serialization_fence",
        "lock_invocation_gc_serialization_fence",
        "CAS hard-deny",
        "R11-F1-C destructive implementation remains HOLD",
    ):
        assert phrase in text


REPAIR_C_CONTRACT = (
    ROOT
    / "docs/agent_execution_r11/"
    "R11_F1C_ABSENT_INVOCATION_FENCE_CCD6B64C.md"
)


def test_r11_f1c_absent_invocation_key_has_cross_process_sql_authority():
    key_source = inspect.getsource(
        CapabilityInvocationRepository.lock_invocation_id_serialization_key
    )
    fence_source = inspect.getsource(
        CapabilityInvocationRepository.lock_invocation_gc_serialization_fence
    )

    assert 'dialect == "postgresql"' in key_source
    assert "pg_advisory_xact_lock" in key_source
    assert "_invocation_advisory_lock_key" in key_source
    assert 'dialect == "sqlite"' in key_source
    assert "where(false())" in key_source
    assert "unsupported" in key_source.lower()
    assert "lock_invocation_id_serialization_key" in fence_source
    assert fence_source.index("lock_invocation_id_serialization_key") < fence_source.index(
        ".with_for_update()"
    )


def test_r11_f1c_r6_create_shares_invocation_key_and_rechecks_agent_binding():
    source = inspect.getsource(SqlCapabilityInvocationStore.create)

    assert "lock_invocation_gc_serialization_fence" in source
    assert "list_agent_tool_call_bindings" in source
    assert "CapabilityInvocation id conflicts with durable " in source
    assert "AgentToolCall ownership." in source
    assert source.index("lock_invocation_gc_serialization_fence") < source.index(
        "list_agent_tool_call_bindings"
    )
    assert source.index("list_agent_tool_call_bindings") < source.index(
        "CapabilityInvocationRecord(**self._values(invocation))"
    )


def test_r11_f1c_repair_c_contract_freezes_absent_key_and_r6_boundary():
    text = REPAIR_C_CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "628f61fd7894e1425daf9a989f4c4494f7492a94",
        "Architecture #1247",
        "P1-R11-F1C-ABSENT-INVOCATION-FENCE-2",
        "pg_advisory_xact_lock",
        "SQLite",
        "Agent fresh save_tool_call",
        "SqlCapabilityInvocationStore.create",
        "CapabilityInvocation lifecycle remains R6-owned",
        "R11-F1-C destructive implementation remains HOLD",
    ):
        assert phrase in text



def test_r11_f1c_capability_repository_defers_agent_model_import():
    source = (
        ROOT
        / "se/src/infrastructure/storage/repositories/"
        "capability_invocations.py"
    ).read_text(encoding="utf-8")

    assert "if TYPE_CHECKING:" in source
    assert (
        source.count(
            "from ..models.sql.agent.tool_call import AgentToolCallRecord"
        )
        == 2
    )
    method_source = inspect.getsource(
        CapabilityInvocationRepository.list_agent_tool_call_bindings
    )
    assert (
        "from ..models.sql.agent.tool_call import AgentToolCallRecord"
        in method_source
    )


def test_r11_f1c_capability_repository_clean_process_import_succeeds():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import "
                "se.src.infrastructure.storage.repositories."
                "capability_invocations"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (
        "clean-process capability_invocations import failed:\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
