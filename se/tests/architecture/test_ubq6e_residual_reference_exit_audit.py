from __future__ import annotations

from pathlib import Path

from cl.src.schemas.agent_execution import (
    AgentExecutionLimits as ClientAgentExecutionLimits,
)
from se.src.domain.schemas.agent_execution import (
    AgentExecutionLimits as ServerAgentExecutionLimits,
)
from se.src.domain.schemas.task_budget import TaskBudgetReservationKind
from se.src.infrastructure.config.schemas import ConfigSchema


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/user_budget_quota/"
    "UBQ_6E_RESIDUAL_REFERENCE_EXIT_AUDIT_8F37A7CD.md"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_ubq6e_contract_is_exact_zero_production_exit_audit() -> None:
    text = _contract()

    for token in (
        "ZERO-PRODUCTION / CONTRACT + ARCHITECTURE EVIDENCE ONLY",
        "Issue #148 comment #6021466396",
        "UBQ-6A through UBQ-6D are already LANDED / CANONICAL / HEALTHY",
        "No third path is authorized.",
        "Production authority: **NONE**.",
        "Runtime authority: **NONE**.",
        "Configuration authority: **NONE**.",
        "Schema/migration authority: **NONE**.",
        "Client authority: **NONE**.",
        "UBQ-7 authority: **NONE**.",
        "Merge authority: **NONE**",
    ):
        assert token in text

    assert (
        "docs/user_budget_quota/"
        "UBQ_6E_RESIDUAL_REFERENCE_EXIT_AUDIT_8F37A7CD.md"
    ) in text
    assert (
        "se/tests/architecture/"
        "test_ubq6e_residual_reference_exit_audit.py"
    ) in text


def test_ubq6e_preserves_landed_6c_config_split_without_user_policy_authority() -> None:
    settings = ConfigSchema().agent.task_budget

    assert set(settings.model_dump()) == {
        "policy_version",
        "execution_guards",
        "legacy_resource_fallback",
    }
    assert settings.execution_guards.max_total_executions == 64
    assert settings.execution_guards.max_active_executions == 8
    assert settings.legacy_resource_fallback.max_total_tool_calls == 256
    assert settings.legacy_resource_fallback.max_total_inference_calls == 128

    schemas = _text("se/src/infrastructure/config/schemas.py")
    main = _text("se/src/main.py")

    assert "class AgentTaskExecutionGuardSettings" in schemas
    assert "class LegacyTaskBudgetResourceFallbackSettings" in schemas
    assert "def normalize_legacy_flat_input" in schemas

    composition_start = main.index("task_budget_settings = config.agent.task_budget")
    composition_end = main.index(
        "dual_accounting_settings =",
        composition_start,
    )
    composition = main[composition_start:composition_end]

    for token in (
        "execution_guard_settings = task_budget_settings.execution_guards",
        "legacy_resource_fallback_settings =",
        "task_budget_settings.legacy_resource_fallback",
        "max_total_tool_calls=(",
        "legacy_resource_fallback_settings.max_total_tool_calls",
        "max_total_inference_calls=(",
        "legacy_resource_fallback_settings.max_total_inference_calls",
    ):
        assert token in composition

    assert "UserBudgetPolicy" not in composition
    assert "user_budget" not in composition


def test_ubq6e_pins_feature_on_ubq_vs_feature_off_taskbudget_fallback() -> None:
    runtime = _text("se/src/runtimes/agent/task_budget.py")
    main = _text("se/src/main.py")
    landed_evidence = _text(
        "se/tests/integration/test_ubq6a_taskbudget_resource_demotion.py"
    )

    assert "user_tool_quota_enabled: bool = False" in runtime
    assert "user_inference_quota_enabled: bool = False" in runtime
    assert "if self._user_tool_quota_enabled:" in runtime
    assert "if self._user_inference_quota_enabled:" in runtime
    assert "elif proposed > budget.limits.max_total_tool_calls:" in runtime
    assert '"max_total_tool_calls exceeded"' in runtime
    assert "max_total_inference_calls reached" in runtime

    assert "user_tool_quota_enabled=tool_quota_settings.enabled" in main
    assert "user_inference_quota_enabled=inference_quota_settings.enabled" in main

    assert (
        "test_ubq6a_feature_off_preserves_finite_taskbudget_guards"
        in landed_evidence
    )
    assert (
        "test_ubq6a_canonical_modes_saturate_compatibility_counters_and_replay"
        in landed_evidence
    )
    assert (
        "test_ubq6a_tool_and_inference_demotion_switches_are_independent"
        in landed_evidence
    )

    contract = _contract()
    assert "feature-ON canonical UBQ authority" in contract
    assert "feature-OFF finite TaskBudget fallback" in contract


def test_ubq6e_retains_taskbudget_history_and_reservation_identity() -> None:
    domain = _text("se/src/domain/schemas/task_budget.py")
    model = _text("se/src/infrastructure/storage/models/sql/agent/task_budget.py")
    migration = _text(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "11a_r5_task_budget.py"
    )
    contract = _contract()

    for field in (
        "max_total_tool_calls",
        "max_total_inference_calls",
        "max_total_tokens",
        "max_total_cost_usd",
        "used_tool_calls",
        "used_inference_calls",
        "used_tokens",
        "used_cost_usd",
        "policy_version",
        "policy_fingerprint",
    ):
        assert field in domain
        assert field in model
        assert field in contract

    assert TaskBudgetReservationKind.TOOL_CALL.value == "TOOL_CALL"
    assert TaskBudgetReservationKind.INFERENCE.value == "INFERENCE"
    assert TaskBudgetReservationKind.USAGE.value == "USAGE"

    for token in (
        'sa.Column("max_total_tool_calls"',
        'sa.Column("max_total_inference_calls"',
        'sa.Column("max_total_tokens"',
        'sa.Column("max_total_cost_usd"',
    ):
        assert token in migration

    assert "immutable migration history" in contract
    assert "Destructive compatibility cleanup remains HOLD" in contract


def test_ubq6e_preserves_landed_6d_wire_compatibility() -> None:
    for limits_cls in (
        ServerAgentExecutionLimits,
        ClientAgentExecutionLimits,
    ):
        defaults = limits_cls()
        assert defaults.max_tool_calls == 16
        assert defaults.max_cost is None
        assert defaults.legacy_max_cost is None
        assert "max_cost" in limits_cls.model_fields
        assert "legacy_max_cost" not in limits_cls.model_fields

        explicit = limits_cls.model_validate(
            {
                "max_tool_calls": 7,
                "max_cost": 3.5,
            }
        )
        assert explicit.max_tool_calls == 7
        assert explicit.max_cost == 3.5
        assert explicit.legacy_max_cost == 3.5

        wire = explicit.model_dump(mode="json")
        assert wire["max_cost"] == 3.5
        assert "legacy_max_cost" not in wire

        schema = limits_cls.model_json_schema()["properties"]
        assert "max_cost" in schema
        assert "legacy_max_cost" not in schema


def test_ubq6e_keeps_timeout_and_provider_retry_outside_renewable_quota() -> None:
    provider = _text("se/src/provider/retry_contracts.py")
    timeout_contract = _text(
        "docs/user_budget_quota/USER_RESOURCE_BUDGET_TIMEOUT_REFREEZE.md"
    )
    contract = _contract()

    provider_start = provider.index("class ProviderCallBudget:")
    provider_block = provider[provider_start : provider_start + 1000]

    assert (
        "Process-local deadline and retry budget for one logical provider call."
        in provider_block
    )
    assert "One instance is shared across every fallback candidate" in provider_block
    assert (
        "RESOURCE BUDGET     = renewable resource quota owned by authenticated user"
        in timeout_contract
    )
    assert (
        "TIMEOUTS/DEADLINES  = bounded waiting and wall/active-time safety controls"
        in timeout_contract
    )
    assert (
        "execution/iteration/task time controls are guards/deadlines, not renewable quota"
        in timeout_contract
    )
    assert "Timeout/deadline remains distinct from renewable resource quota." in contract


def test_ubq6e_inventories_legacy_ubq2_admission_wording_without_promoting_it() -> None:
    user_budget = _text("se/src/application/user_budget.py")
    contract = _contract()

    assert "TaskBudget remains admission authority." in user_budget
    assert "inventoried historical/compatibility" in contract
    assert "MUST NOT" in contract
    assert "use this legacy sentence to mint, restore, or broaden TaskBudget renewable" in contract.replace("\n", " ")


def test_ubq6e_keeps_postgresql_v7_gate_open_and_mandatory() -> None:
    postgres_gate = _text(
        "docs/user_budget_quota/UBQ_2_POSTGRESQL_V7_EVIDENCE_GATE.md"
    )
    contract = _contract()

    assert (
        "Status: **OPEN / REQUIRED BEFORE ANY POSTGRESQL 26a / "
        "UBQ-2 RUNTIME DEPLOYMENT**"
        in postgres_gate
    )
    assert "26a_ubq2_dual_accounting_bridge" in postgres_gate
    assert "is BLOCKED" in postgres_gate
    assert "Linux/Windows Architecture success is not executable PostgreSQL parity." in contract
    assert "UBQ-6E does not close, waive, weaken, or relocate this deployment gate." in contract


def test_ubq6e_records_aos2_only_as_future_ubq7_exit_dependency() -> None:
    contract = _contract()

    assert "Issue #167 / AOS-2 Agent-only production cutover" in contract
    assert "**MATERIAL future UBQ-7" in contract
    assert "It is not a blocker to this zero-production UBQ-6E residual audit." in contract
    assert "No #167, #156, CAS, CTX, AE, or TBO authority transfers to UBQ-6E." in contract
