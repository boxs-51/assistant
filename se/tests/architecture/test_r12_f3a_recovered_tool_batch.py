from __future__ import annotations

import inspect

from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_execution import (
    AgentRecoveryExecutionService,
)
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)


def test_r12_f3a_exact_activation_expiry_is_part_of_durable_fence():
    repository_source = inspect.getsource(
        AgentRepository.has_active_execution_lease_fence
    )
    store_source = inspect.getsource(
        DurableAgentStore.has_active_execution_lease_fence
    )

    assert "expected_lease_expires_at" in repository_source
    assert "lease_expires_at ==" in repository_source
    assert "expected_lease_expires_at" in store_source


def test_r12_f3a_pre_dispatch_guard_runs_inside_continuation_semaphore():
    source = inspect.getsource(
        AgentToolExecutionCoordinator.continue_invocations
    )

    semaphore_index = source.index("async with semaphore")
    prepare_index = source.index("await pre_dispatch_prepare")
    dispatch_index = source.index(
        "return await self.continue_invocation(context, action)"
    )
    assert semaphore_index < prepare_index < dispatch_index
    assert "preserve_started_on_failure" in source
    assert "dispatch_started" in source


def test_r12_f3a_reuses_r7_r6_path_and_stops_before_provider_inference():
    source = inspect.getsource(
        AgentRecoveryExecutionService.execute_active_tool_batch
    )
    prepare_source = inspect.getsource(
        AgentRecoveryExecutionService._prepare_continuation_action
    )
    projection_source = inspect.getsource(
        AgentRecoveryExecutionService._project_continuation_result
    )

    assert "continue_invocations" in source
    assert "pre_dispatch_prepare=prepare" in source
    assert "preserve_started_on_failure=True" in source
    assert "_resolve_continuation_target" in prepare_source
    assert "implementation_id" in prepare_source
    assert "expected_lease_expires_at" in inspect.getsource(
        AgentRecoveryExecutionService._require_exact_active_fence
    )
    assert "save_tool_result" in projection_source
    assert "_require_exact_active_fence" in projection_source

    combined = source + prepare_source + projection_source
    assert "execute_capability(" not in combined
    assert "ProviderInferenceAdapter" not in combined
    assert "recover_claimed_resume" not in combined
    assert "fail_claimed_resume" not in combined
