from decimal import Decimal

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
    task_budget_policy_fingerprint,
)
from se.src.infrastructure.config.schemas import ConfigSchema
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.task_budget import TaskBudgetService


def _policy_from_config(config: ConfigSchema):
    settings = config.agent.task_budget
    execution_guards = settings.execution_guards
    legacy_resource_fallback = settings.legacy_resource_fallback
    limits = TaskBudgetLimits(
        max_total_executions=execution_guards.max_total_executions,
        max_active_executions=execution_guards.max_active_executions,
        max_active_branches=execution_guards.max_active_branches,
        max_parallel_agents=execution_guards.max_parallel_agents,
        max_total_tool_calls=legacy_resource_fallback.max_total_tool_calls,
        max_total_inference_calls=(
            legacy_resource_fallback.max_total_inference_calls
        ),
        max_total_tokens=legacy_resource_fallback.max_total_tokens,
        max_total_cost_usd=legacy_resource_fallback.max_total_cost_usd,
        max_delegation_depth=execution_guards.max_delegation_depth,
    )
    policy = TaskBudgetPolicy(
        version=settings.policy_version,
        deny_recursive_agent_cycle=execution_guards.deny_recursive_agent_cycle,
    )
    return limits, policy


def test_r5_c1_application_config_is_task_budget_authority():
    config = ConfigSchema()
    limits, policy = _policy_from_config(config)

    assert limits.max_total_executions == 64
    assert limits.max_active_executions == 8
    assert limits.max_parallel_agents == 4
    assert limits.max_total_tool_calls == 256
    assert limits.max_total_inference_calls == 128
    assert limits.max_total_tokens == 1_000_000
    assert limits.max_total_cost_usd is None
    assert limits.max_delegation_depth == 8
    assert policy.version == "r5-v1"


def test_r5_c1_task_budget_config_uses_explicit_nested_groups():
    settings = ConfigSchema().agent.task_budget

    assert set(settings.model_dump()) == {
        "policy_version",
        "execution_guards",
        "legacy_resource_fallback",
    }
    assert settings.execution_guards.max_active_branches == 16
    assert settings.legacy_resource_fallback.max_total_tool_calls == 256


def test_r5_c1_legacy_flat_task_budget_input_maps_to_nested_groups():
    config = ConfigSchema.model_validate(
        {
            "agent": {
                "task_budget": {
                    "policy_version": "legacy-input-v1",
                    "deny_recursive_agent_cycle": False,
                    "max_total_executions": 72,
                    "max_active_executions": 9,
                    "max_active_branches": 17,
                    "max_parallel_agents": 5,
                    "max_delegation_depth": 10,
                    "max_total_tool_calls": 333,
                    "max_total_inference_calls": 177,
                    "max_total_tokens": 1_500_000,
                    "max_total_cost_usd": "12.50",
                }
            }
        }
    )
    settings = config.agent.task_budget

    assert settings.policy_version == "legacy-input-v1"
    assert settings.execution_guards.deny_recursive_agent_cycle is False
    assert settings.execution_guards.max_total_executions == 72
    assert settings.execution_guards.max_active_executions == 9
    assert settings.execution_guards.max_active_branches == 17
    assert settings.execution_guards.max_parallel_agents == 5
    assert settings.execution_guards.max_delegation_depth == 10
    assert settings.legacy_resource_fallback.max_total_tool_calls == 333
    assert settings.legacy_resource_fallback.max_total_inference_calls == 177
    assert settings.legacy_resource_fallback.max_total_tokens == 1_500_000
    assert settings.legacy_resource_fallback.max_total_cost_usd == Decimal("12.50")


def test_r5_c1_legacy_flat_input_overrides_nested_merged_defaults():
    config = ConfigSchema.model_validate(
        {
            "agent": {
                "task_budget": {
                    "execution_guards": {
                        "max_total_executions": 64,
                    },
                    "legacy_resource_fallback": {
                        "max_total_tool_calls": 256,
                    },
                    "max_total_executions": 91,
                    "max_total_tool_calls": 444,
                }
            }
        }
    )

    assert config.agent.task_budget.execution_guards.max_total_executions == 91
    assert (
        config.agent.task_budget.legacy_resource_fallback.max_total_tool_calls
        == 444
    )


def test_r5_c1_policy_fingerprint_is_stable_across_service_injection():
    config = ConfigSchema()
    limits, policy = _policy_from_config(config)
    service = TaskBudgetService(
        lambda: None,
        default_limits=limits,
        default_policy=policy,
    )

    assert service.default_limits is limits
    assert service.default_policy is policy
    assert task_budget_policy_fingerprint(
        service.default_limits,
        service.default_policy,
    ) == task_budget_policy_fingerprint(limits, policy)
    assert (
        service.default_limits.max_total_cost_usd is None
        or isinstance(
            service.default_limits.max_total_cost_usd,
            Decimal,
        )
    )


@pytest.mark.asyncio
async def test_r5_c1_coordinator_uses_atomic_task_budget_creation_path():
    class BudgetService:
        def __init__(self):
            self.values = None

        async def create_task_with_budget(self, values, *, identity=None):
            self.values = dict(values)
            self.identity = identity

    class LegacyStore:
        async def save_task(self, values):
            raise AssertionError(
                "R5-C production task creation must not use save_task alone"
            )

    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name="agent-r5-c",
            goal="test",
            instruction="test",
        )
    )
    identity = Identity(
        user_id="user-r5-c",
        auth_type="api_key",
        scopes={"*"},
    )
    budget_service = BudgetService()
    coordinator = MultiAgentCoordinator(
        registry,
        durable_store=LegacyStore(),
        task_budget_service=budget_service,
    )
    session = coordinator.create_session(identity, ["agent-r5-c"])

    task = await coordinator.create_task_async(
        session_id=session.session_id,
        assigned_agent_id="agent-r5-c",
        task_input={"prompt": "hello"},
        identity=identity,
    )

    assert budget_service.values["id"] == task.task_id
    assert budget_service.values["revision"] == 0
    assert budget_service.values["status"] == "ASSIGNED"
    assert budget_service.identity is identity
