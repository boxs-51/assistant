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
from se.src.runtimes.capability.drivers.remote_client_driver import (
    RemoteClientDriver,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


def test_r12_f3a_exact_activation_expiry_is_part_of_durable_fence():
    repository_source = inspect.getsource(
        AgentRepository.has_active_execution_lease_fence
    )
    store_source = inspect.getsource(
        DurableAgentStore.has_active_execution_lease_fence
    )

    normalized_repository = " ".join(repository_source.split())
    assert "expected_lease_expires_at" in repository_source
    assert (
        "AgentExecutionRecord.lease_expires_at == expected_lease_expires_at"
        in normalized_repository
    )
    assert "expected_lease_expires_at" in store_source


def test_r12_f3a_pre_dispatch_guard_runs_inside_continuation_semaphore():
    source = inspect.getsource(
        AgentToolExecutionCoordinator.continue_invocations
    )

    normalized = " ".join(source.split())
    semaphore_index = normalized.index("async with semaphore")
    prepare_index = normalized.index("await pre_dispatch_prepare")
    dispatch_index = normalized.index(
        "return await self.continue_invocation( context, action"
    )
    assert semaphore_index < prepare_index < dispatch_index
    assert "preserve_started_on_failure" in source
    assert "continuation_started" in source
    assert "physical_dispatch_authorized" in source
    assert source.count("prepare_failed.is_set()") >= 4


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
    handoff_source = inspect.getsource(
        AgentRecoveryExecutionService._require_consumed_activation_handoff
    )
    assert "r12_f2_activation_handoff" in handoff_source
    assert "recovery_plan_fingerprint" in handoff_source
    assert "recovery_fingerprint" in handoff_source
    assert "save_tool_result" in projection_source
    assert "_require_exact_active_fence" in projection_source

    combined = source + prepare_source + projection_source
    assert "execute_capability(" not in combined
    assert "ProviderInferenceAdapter" not in combined
    assert "recover_claimed_resume" not in combined
    assert "fail_claimed_resume" not in combined


def test_r12_f3a_does_not_create_second_quota_or_lease_lifecycle():
    service_source = inspect.getsource(AgentRecoveryExecutionService)
    assert "find_tool_call_authority" in service_source
    assert "reserve_tool_call" not in service_source
    assert "recover_tool_call" not in service_source
    assert "renew_execution_lease" not in service_source
    assert "release_execution_lease" not in service_source
    assert "acquire_execution_lease" not in service_source
    assert "execute_capability(" not in service_source


def test_r12_f3a_canonical_guard_is_last_await_before_remote_send():
    bind_source = inspect.getsource(
        CapabilityRuntime._bind_remote_dispatch_started
    )
    mark_index = bind_source.index(
        "await self.invocation_lifecycle.update_remote_outcome"
    )
    guard_index = bind_source.index(
        "await continuation_dispatch_guard"
    )
    assert mark_index < guard_index

    driver_source = inspect.getsource(RemoteClientDriver.execute)
    handler_index = driver_source.index(
        "observed = self._dispatch_started_handler"
    )
    handler_await = driver_source.index(
        "await observed",
        handler_index,
    )
    send_index = driver_source.index(
        "return await self._realtime.invoke",
        handler_await,
    )
    assert handler_index < handler_await < send_index
    assert driver_source[handler_await:send_index].count("await ") == 1

    revalidator = inspect.getsource(
        CapabilityRuntime._revalidate_continuation_target_at_dispatch
    )
    assert "target_connection_id" in revalidator
    assert "origin_client_id" in revalidator
    assert "list_implementations" in revalidator


def test_r12_f3a_projection_fence_is_transactional_with_agent_write():
    fence_source = inspect.getsource(
        DurableAgentStore._lock_recovery_projection_fence_in_uow
    )
    fence_now_source = inspect.getsource(
        DurableAgentStore._require_recovery_projection_fence_now
    )
    save_source = inspect.getsource(DurableAgentStore.save_tool_result)
    promote_source = inspect.getsource(
        DurableAgentStore.load_committed_tool_result
    )
    projection_source = inspect.getsource(
        AgentRecoveryExecutionService._project_continuation_result
    )

    assert "get_execution_for_update" in fence_source
    assert "_require_recovery_projection_fence_now" in fence_source
    assert 'str(execution.state) != "RUNNING"' in fence_now_source
    assert "owner_instance_id" in fence_now_source
    assert "lease_generation" in fence_now_source
    assert "lease_expires_at" in fence_now_source
    assert "_utc_now()" in fence_now_source

    save_fence = save_source.index(
        "await self._lock_recovery_projection_fence_in_uow"
    )
    save_write = min(
        index
        for index in (
            save_source.find("await uow.agents.save_tool_result"),
            save_source.find("await uow.agents.update_tool_result"),
        )
        if index >= 0
    )
    assert save_fence < save_write

    promote_fence = promote_source.index(
        "await self._lock_recovery_projection_fence_in_uow"
    )
    promote_write = promote_source.index(
        "await uow.agents.update_tool_result"
    )
    assert promote_fence < promote_write
    assert save_source.count(
        "_require_recovery_projection_fence_now"
    ) >= 2
    assert promote_source.count(
        "_require_recovery_projection_fence_now"
    ) >= 1

    assert "recovery_fence=recovery_fence" in projection_source
