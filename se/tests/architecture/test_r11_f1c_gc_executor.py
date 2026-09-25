from __future__ import annotations

import inspect
from pathlib import Path

from se.src.runtimes.agent import gc_executor
from se.src.runtimes.agent.gc_executor import (
    AgentGcExecutor,
    execute_task_gc_in_uow,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/agent_execution_r11/"
    "R11_F1C_TRANSACTIONAL_GC_EXECUTOR_CCD6B64C.md"
)


def test_r11_f1c_executor_freezes_exact_canonical_integration_baseline():
    text = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "STACK_POLICY_ISSUE    = #85",
        "STACK_POLICY_VERSION  = v1",
        "CANONICAL_MAIN_HEAD         = f58ce30b73488be4fb861cdcb3d613b509005c0e",
        "CANONICAL_MAIN_ARCHITECTURE = #1298 GREEN/GREEN",
        "UPSTREAM_RELEASE_PR         = #86 MERGED",
        "PR82_REFRESH_BASE           = f58ce30b73488be4fb861cdcb3d613b509005c0e",
        "INTEGRATION_STATUS          = CANONICAL_REFRESH / EXACT-MAIN CI + AUDIT REQUIRED",
        "MERGE_AUTHORIZATION         = GRANTED CONDITIONALLY / USER",
        "work/ae-r11-f1-c-ccd6b64c",
    ):
        assert phrase in text

def test_r11_f1c_executor_locks_then_revalidates_before_delete():
    source = inspect.getsource(execute_task_gc_in_uow)

    assert "lock_task_gc_serialization_fence" in source
    assert source.count("build_task_gc_dry_run_in_uow") >= 2
    assert "lock_invocation_gc_serialization_fence" in source
    assert "fresh.fingerprint != probe.fingerprint" in source
    assert "expected_fingerprint" in source

    first_revalidate = source.index("build_task_gc_dry_run_in_uow")
    invocation_fence = source.index("lock_invocation_gc_serialization_fence")
    second_revalidate = source.index(
        "build_task_gc_dry_run_in_uow",
        first_revalidate + 1,
    )
    first_delete = source.index("_delete_exact")

    assert first_revalidate < invocation_fence < second_revalidate < first_delete


def test_r11_f1c_executor_is_fail_closed_on_rowcount_and_scope_drift():
    source = inspect.getsource(gc_executor)

    assert "R11-F1-C rowcount drift" in source
    assert "unopened row kinds" in source
    assert "branch lineage cycle" in source
    assert "final rowcount mismatch" in source
    assert "await session.flush()" in source


def test_r11_f1c_executor_deletes_task_last_and_never_imports_transcript_rows():
    source = inspect.getsource(execute_task_gc_in_uow)
    module_source = inspect.getsource(gc_executor)

    task_delete = source.rindex("AgentTaskRecord")
    budget_delete = source.rindex("TaskBudgetRecord")
    execution_delete = source.rindex("AgentExecutionRecord")

    assert execution_delete < budget_delete < task_delete
    assert "AgentTranscriptRepresentationRecord" not in module_source
    assert "AgentTranscriptPayloadNodeRecord" not in module_source
    assert "AgentTranscriptChunkRecord" not in module_source


def test_r11_f1c_service_commits_only_after_executor_and_rolls_back_on_error():
    source = inspect.getsource(AgentGcExecutor.collect_task)

    assert source.index("execute_task_gc_in_uow") < source.index("uow.commit")
    assert "except Exception" in source
    assert "rollback" in source


def test_r11_f1c_contract_preserves_r6_r12_cas_and_merge_boundaries():
    text = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "ClientInvocationLedger remains R6-owned",
        "no R12 lease/recovery semantics",
        "no CAS FileAsset/FileBlob/FileReference/FileProviderBinding",
        "transcript representation/payload/chunks remain retained",
        "production merge authorization = GRANTED CONDITIONALLY",
        "UPSTREAM_RELEASE_PR         = #86 MERGED",
        "No R11 production grandchild before #82 canonical landing",
    ):
        assert phrase in text
