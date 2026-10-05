from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_ubq6a_exposes_exact_shadow_policy_classifier() -> None:
    source = _text("se/src/application/user_budget.py")
    assert "def is_recognized_shadow_policy(self, row) -> bool:" in source
    assert "return self._is_recognized_shadow_policy(row)" in source
    assert '"authority": "UBQ2_SHADOW_ACCOUNTING"' in source
    assert 'version.startswith("ubq2-shadow-")' in source


def test_ubq6a_new_canonical_admissions_reject_shadow_policy_before_charge() -> None:
    tool = _text("se/src/application/user_tool_quota.py")
    inference = _text("se/src/application/user_inference_quota.py")

    tool_shadow = tool.index(
        "self._owner_authority.is_recognized_shadow_policy(policy)"
    )
    tool_charge = tool.index(
        "create_or_get_tool_usage",
        tool_shadow,
    )
    assert tool_shadow < tool_charge
    assert (
        "automatic UBQ-2 shadow policy cannot authorize "
        in tool[tool_shadow:tool_charge]
    )

    inference_shadow = inference.index(
        "self._owner_authority.is_recognized_shadow_policy(policy)",
        inference.index("async def reserve("),
    )
    inference_charge = inference.index(
        "pending_input = await",
        inference_shadow,
    )
    assert inference_shadow < inference_charge
    assert (
        "automatic UBQ-2 shadow policy cannot authorize "
        in inference[inference_shadow:inference_charge]
    )


def test_ubq6a_taskbudget_resource_veto_is_feature_scoped_and_saturating() -> None:
    source = _text("se/src/runtimes/agent/task_budget.py")

    batch_start = source.index("async def reserve_tool_call_batch(")
    batch_end = source.index("async def start_task_scoped_execution(", batch_start)
    batch = source[batch_start:batch_end]
    assert "if self._user_tool_quota_enabled:" in batch
    assert "proposed = min(" in batch
    assert "elif proposed > budget.limits.max_total_tool_calls:" in batch
    assert "TaskBudgetReservationKind.TOOL_CALL.value" in batch

    inference_start = source.index("async def reserve_inference(")
    inference_end = source.index("async def account_usage(", inference_start)
    inference = source[inference_start:inference_end]
    assert "if self._user_inference_quota_enabled:" in inference
    assert '"used_inference_calls": min(' in inference
    assert '"max_total_inference_calls reached"' in inference
    assert '"max_total_tokens reached"' in inference
    assert '"max_total_cost_usd reached"' in inference
    assert "TaskBudgetReservationKind.INFERENCE" in inference


def test_ubq6a_preserves_structural_taskbudget_and_main_wiring_contract() -> None:
    task_budget = _text("se/src/runtimes/agent/task_budget.py")
    main = _text("se/src/main.py")
    domain = _text("se/src/domain/schemas/task_budget.py")

    for token in (
        "TaskBudgetReservationKind.NEW_EXECUTION",
        "TaskBudgetReservationKind.RESUME_EXECUTION",
        "TaskBudgetReservationKind.RELEASE_EXECUTION",
        "TaskBudgetReservationKind.BRANCH",
        "TaskBudgetReservationKind.RELEASE_BRANCH",
        "max_active_executions",
        "max_active_branches",
        "max_parallel_agents",
        "max_delegation_depth",
    ):
        assert token in task_budget or token in domain

    assert "user_tool_quota_enabled=tool_quota_settings.enabled" in main
    assert "user_inference_quota_enabled=inference_quota_settings.enabled" in main
    assert "def task_budget_policy_fingerprint(" in domain
    assert '"limits": limits.model_dump(mode="json")' in domain


def test_ubq6a_canonical_quota_runs_before_physical_dispatch() -> None:
    capability = _text("se/src/runtimes/capability/runtime.py")
    chat = _text("se/src/provider/handlers/chat_handler.py")

    tool_execute = capability.index("async def execute_capability(")
    tool_reserve = capability.index(
        "self.tool_quota_service.reserve_tool_call(",
        tool_execute,
    )
    tool_dispatch = capability.index(
        "await self._run_invocation_attempt(",
        tool_reserve,
    )
    assert tool_reserve < tool_dispatch

    provider_execute = chat.index("async def execute_with_fallback(")
    inference_reserve = chat.index(
        "admission = await self._reserve_inference_quota(",
        provider_execute,
    )
    physical_dispatch = chat.index(
        "response = await self.executor.execute(",
        inference_reserve,
    )
    assert inference_reserve < physical_dispatch
