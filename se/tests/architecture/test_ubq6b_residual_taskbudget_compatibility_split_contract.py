from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    "docs/user_budget_quota/"
    "UBQ_6B_RESIDUAL_TASKBUDGET_COMPATIBILITY_SPLIT_FREEZE_E2395DAB.md"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_ubq6b_freezes_mixed_taskbudget_config_as_future_migration_seam() -> None:
    schemas = _text("se/src/infrastructure/config/schemas.py")
    default = _text("se/config/default.yaml")
    main = _text("se/src/main.py")

    settings_start = schemas.index("class AgentTaskBudgetSettings")
    settings_end = schemas.index("class AgentSettings", settings_start)
    settings = schemas[settings_start:settings_end]

    structural = (
        "deny_recursive_agent_cycle",
        "max_total_executions",
        "max_active_executions",
        "max_active_branches",
        "max_parallel_agents",
        "max_delegation_depth",
    )
    resource_fallback = (
        "max_total_tool_calls",
        "max_total_inference_calls",
        "max_total_tokens",
        "max_total_cost_usd",
    )

    for token in structural + resource_fallback:
        assert token in settings

    agent_start = default.index("\nagent:\n")
    agent_end = default.index("\noauth:", agent_start)
    agent_block = default[agent_start:agent_end]
    assert "task_budget:" in agent_block
    for token in structural + resource_fallback:
        assert token in agent_block

    composition_start = main.index("task_budget_settings = config.agent.task_budget")
    composition_end = main.index("dual_accounting_settings =", composition_start)
    composition = main[composition_start:composition_end]
    for token in structural + resource_fallback:
        assert token in composition

    assert "UserBudgetPolicy" not in composition
    assert "user_budget" not in settings


def test_ubq6b_preserves_taskbudget_resource_history_as_compatibility_state() -> None:
    domain = _text("se/src/domain/schemas/task_budget.py")
    model = _text(
        "se/src/infrastructure/storage/models/sql/agent/task_budget.py"
    )
    migration = _text(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "11a_r5_task_budget.py"
    )

    max_fields = (
        "max_total_tool_calls",
        "max_total_inference_calls",
        "max_total_tokens",
        "max_total_cost_usd",
    )
    used_fields = (
        "used_tool_calls",
        "used_inference_calls",
        "used_tokens",
        "used_cost_usd",
    )

    for token in max_fields + used_fields:
        assert token in domain
        assert token in model
        assert token in migration

    for token in (
        'TOOL_CALL = "TOOL_CALL"',
        'INFERENCE = "INFERENCE"',
        'USAGE = "USAGE"',
    ):
        assert token in domain

    assert "def task_budget_policy_fingerprint(" in domain
    assert '"limits": limits.model_dump(mode="json")' in domain
    assert '"policy": policy.model_dump(mode="json")' in domain
    assert "policy_version" in model
    assert "policy_fingerprint" in model
    assert 'revision: str = "11a_r5_task_budget"' in migration


def test_ubq6b_keeps_structural_guards_and_feature_scoped_resource_fallback() -> None:
    domain = _text("se/src/domain/schemas/task_budget.py")
    runtime = _text("se/src/runtimes/agent/task_budget.py")
    main = _text("se/src/main.py")

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
        assert token in runtime or token in domain

    tool_start = runtime.index("async def reserve_tool_call_batch(")
    tool_end = runtime.index(
        "async def reconcile_multibranch_task_activity(",
        tool_start,
    )
    tool_block = runtime[tool_start:tool_end]
    assert "if self._user_tool_quota_enabled:" in tool_block
    assert "elif proposed > budget.limits.max_total_tool_calls:" in tool_block

    inference_start = runtime.index("async def reserve_inference(")
    inference_end = runtime.index("async def account_usage(", inference_start)
    inference_block = runtime[inference_start:inference_end]
    assert "if self._user_inference_quota_enabled:" in inference_block
    assert '"max_total_inference_calls reached"' in inference_block
    assert '"max_total_tokens reached"' in inference_block
    assert '"max_total_cost_usd reached"' in inference_block

    assert "user_tool_quota_enabled=tool_quota_settings.enabled" in main
    assert "user_inference_quota_enabled=inference_quota_settings.enabled" in main


def test_ubq6b_freezes_agent_execution_resource_vocabulary_without_ubq_authority() -> None:
    server_path = ROOT / "se/src/domain/schemas/agent_execution.py"
    client_path = ROOT / "cl/src/schemas/agent_execution.py"
    server = server_path.read_text(encoding="utf-8")
    client = client_path.read_text(encoding="utf-8")

    for source in (server, client):
        assert "class AgentExecutionLimits" in source
        assert "max_tool_calls: int = 16" in source
        assert "max_cost: Optional[float] = None" in source

    consumer_pattern = re.compile(r"\.max_cost\b")
    consumers: list[str] = []
    excluded = {server_path.resolve(), client_path.resolve()}

    for production_root in (ROOT / "se/src", ROOT / "cl/src"):
        for path in production_root.rglob("*.py"):
            if path.resolve() in excluded:
                continue
            source = path.read_text(encoding="utf-8")
            if consumer_pattern.search(source):
                consumers.append(path.relative_to(ROOT).as_posix())

    assert consumers == []


def test_ubq6b_keeps_budget_configure_time_only_and_provider_budget_external() -> None:
    context = _text("se/src/runtimes/agent/adapters/context.py")
    provider = _text("se/src/provider/retry_contracts.py")

    configure_start = context.index('name="agent.budget.configure"')
    configure_end = context.index("\n        metadata = {", configure_start)
    configure = context[configure_start:configure_end]

    assert "Allocate time for each Agent iteration, model call, and tool call." in configure
    assert "cannot reset the deadline" in configure
    lowered = configure.lower()
    for forbidden in ("user quota", "token quota", "cost quota"):
        assert forbidden not in lowered

    provider_start = provider.index("class ProviderCallBudget:")
    provider_block = provider[provider_start : provider_start + 900]
    assert "Process-local deadline and retry budget for one logical provider call." in provider_block
    assert "One instance is shared across every fallback candidate" in provider_block
    assert "try_consume_retry" in provider_block


def test_ubq6b_contract_keeps_pg_gate_open_and_followons_unreleased() -> None:
    pg_gate = _text(
        "docs/user_budget_quota/UBQ_2_POSTGRESQL_V7_EVIDENCE_GATE.md"
    )
    contract = _text(CONTRACT)

    assert (
        "Status: **OPEN / REQUIRED BEFORE ANY POSTGRESQL 26a / "
        "UBQ-2 RUNTIME DEPLOYMENT**"
    ) in pg_gate
    assert "SQLite CI remains valid SQLite evidence only" in pg_gate
    assert "MUST NOT be presented as PostgreSQL parity" in pg_gate
    assert "setting `user_budget.dual_accounting.enabled=false`" in pg_gate

    for token in (
        "UBQ-6C  structural guard vs legacy resource-fallback config split",
        "UBQ-6D  AgentExecutionLimits resource-vocabulary compatibility cleanup",
        "UBQ-6E  UBQ-6 residual reference/exit audit",
        "= NOT RELEASED",
        "Production authority: **NONE**.",
        "Schema/migration authority: **NONE**.",
        "Runtime authority: **NONE**.",
        "Configuration authority: **NONE**.",
        "Public DTO/wire authority: **NONE**.",
    ):
        assert token in contract

    assert "destructive compatibility deletion" in contract
    assert "only after UBQ-7" in contract
