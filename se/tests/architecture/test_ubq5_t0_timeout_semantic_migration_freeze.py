from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "user_budget_quota"
    / "UBQ_5_T0_TIMEOUT_SEMANTIC_MIGRATION_FREEZE_EA6C5F00.md"
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _normalized_doc() -> str:
    return " ".join(_doc().split()).lower()


def test_ubq5_t0_is_exact_baseline_contract_evidence_only() -> None:
    text = _doc()

    assert "ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12" in text
    assert "Architecture #1882" in text
    assert "CONTRACT + ARCHITECTURE EVIDENCE ONLY" in text
    assert "production/runtime/schema/migration delta MUST remain ZERO" in text
    assert "production CLAIM = NONE" in text


def test_ubq5_t0_preserves_ae_r10_provider_call_budget_authority() -> None:
    retry_contracts = _read("se/src/provider/retry_contracts.py")
    base = _read("se/src/provider/handlers/base.py")
    executor = _read("se/src/provider/executor.py")
    text = _doc()

    assert "class ProviderCallBudget" in retry_contracts
    assert "_deadline_monotonic" in retry_contracts
    assert "def remaining_seconds" in retry_contracts
    assert "def try_consume_retry" in retry_contracts

    assert "def _new_call_budget" in base
    assert "ProviderCallBudget" in base
    assert "self.timeout" in base

    assert "async def await_with_provider_deadline" in executor
    assert "call_budget: ProviderCallBudget" in executor
    assert "ProviderDeadlineExceededError" in executor

    assert "AE-R10 remains canonical owner" in text
    assert "MUST NOT introduce a second provider retry budget" in text


def test_ubq5_t0_freezes_agent_timeout_compatibility_fields() -> None:
    limits = _read("se/src/domain/schemas/agent_execution.py")
    compatibility_sources = "\n".join(
        [
            _read("se/src/runtimes/agent/contracts/context.py"),
            _read("se/src/runtimes/agent/persistence.py"),
            _read("se/src/runtimes/agent/task_budget.py"),
            _read("se/src/runtimes/agent/retry_planning.py"),
        ]
    )
    text = _doc()

    for field in (
        "timeout_seconds",
        "iteration_timeout_seconds",
        "inference_timeout_seconds",
        "tool_timeout_seconds",
        "task_timeout_seconds",
    ):
        assert field in limits

    assert "remaining_active_budget_seconds" in compatibility_sources
    assert "DELETE = PROHIBITED" in text
    assert "execution guards/deadlines, not" in text


def test_ubq5_t0_freezes_inference_and_tool_timeout_seams() -> None:
    inference = _read("se/src/runtimes/agent/adapters/inference.py")
    tool = _read("se/src/runtimes/agent/adapters/tool.py")
    capability = _read("se/src/runtimes/capability/runtime.py")
    text = _doc()

    assert "request.deadline_monotonic" in inference
    assert "request.timeout_seconds" in inference
    assert "asyncio.TimeoutError" in inference

    assert "tool_timeout_seconds" in tool
    assert "remaining_for_operation" in tool
    assert "CAPABILITY_TIMEOUT" in tool

    assert "timeout_seconds" in capability
    assert "caller_execution_remaining_seconds" in capability
    assert "caller_iteration_remaining_seconds" in capability

    assert "TOOL_CALL_TIMEOUT" in text
    assert "TOOL_IDLE_TIMEOUT" in text
    assert "trustworthy progress" in text


def test_ubq5_t0_freezes_gateway_response_compatibility_without_task_terminalization() -> None:
    router = _read("se/src/transport/gateway/api/v1/chat_router.py")
    text = _doc()

    assert "GATEWAY_RESPONSE_TIMEOUT" in router
    assert '"timeout_scope": "response_wait"' in router
    assert '"timeout_seconds": timeout_val' in router

    assert "RESPONSE_IDLE_TIMEOUT" in text
    assert "RESPONSE_HARD_TIMEOUT" in text
    assert "MUST NOT terminalize a durable Task" in text
    assert "heartbeat/ping MUST NOT count as semantic response progress" in text


def test_ubq5_t0_freezes_future_timeout_taxonomy_and_progress_rules() -> None:
    text = _doc()

    for code in (
        "PROVIDER_FIRST_RESPONSE_TIMEOUT",
        "PROVIDER_STREAM_IDLE_TIMEOUT",
        "PROVIDER_CALL_TIMEOUT",
        "TOOL_CALL_TIMEOUT",
        "TOOL_IDLE_TIMEOUT",
        "RESPONSE_IDLE_TIMEOUT",
        "RESPONSE_HARD_TIMEOUT",
    ):
        assert code in text

    normalized = _normalized_doc()
    assert "heartbeat/keepalive alone must not refresh it" in normalized
    assert "providercallbudget" in normalized
    assert "stronger enclosing hard deadline bounds more specific first-response / idle timeout" in normalized


def test_ubq5_t0_freezes_quota_separation_and_unknown_side_effect_truth() -> None:
    text = _doc()
    normalized = _normalized_doc()

    assert "TIMEOUT / DEADLINE != RENEWABLE RESOURCE QUOTA" in text
    assert "MUST NOT mint a UBQ window" in text
    assert "MUST NOT roll/reset/refill a UBQ window" in text
    assert "MUST NOT refund resource usage" in text
    assert "MUST NOT prove that an unknown external side effect did not occur" in text
    assert "unknown usage remains unknown/reserved" in normalized
    assert "R12 lease expiry likewise MUST NOT mint/reset/refund quota" in text


def test_ubq5_t0_records_material_r12_f3a_overlap_and_production_holds() -> None:
    text = _doc()

    assert "R12-F3-A production PR #202" in text
    assert "PR #202 certified HEAD = 09370d9e539ea32599b3032700b44373e61850d1" in text
    assert "PR #202 squash merge / current main = ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12" in text
    assert "changed files = 13" in text

    for path in (
        "se/src/runtimes/agent/tool_execution/coordinator.py",
        "se/src/runtimes/agent/adapters/tool.py",
        "se/src/runtimes/capability/runtime.py",
        "se/src/runtimes/capability/contracts/error.py",
    ):
        assert path in text

    assert "MATERIAL to this T-0 dependency evidence baseline" in text
    assert "MATERIAL / DIRECT OVERLAP" in text
    assert "changes no production/runtime/schema/migration path" in text
    assert "coordinator-only statement" in text
    assert "UBQ-5B" in text and "AE-R10 PRE-CLAIM audit required" in text
    assert "UBQ-5C" in text and "landed R12-F3-A direct overlap" in text
    assert "fresh bilateral PRE-CLAIM required" in text
    assert "UBQ-5D" in text and "Gateway + AE lifecycle" in text
    assert "UBQ-5E" in text and "compatibility mapping incomplete" in text


def test_ubq5_t0_exact_candidate_scope_is_two_evidence_files() -> None:
    text = _doc()

    assert (
        "docs/user_budget_quota/"
        "UBQ_5_T0_TIMEOUT_SEMANTIC_MIGRATION_FREEZE_EA6C5F00.md"
        in text
    )
    assert (
        "se/tests/architecture/"
        "test_ubq5_t0_timeout_semantic_migration_freeze.py"
        in text
    )
    assert "Production/runtime/schema/migration delta MUST remain ZERO." in text

def test_ubq5_t0_freezes_explicit_compatibility_disposition_matrix() -> None:
    text = _doc()

    assert "Explicit KEEP / MIGRATE / ALIAS / DEPRECATE matrix" in text
    for disposition in ("KEEP", "MIGRATE", "ALIAS", "DEPRECATE"):
        assert disposition in text

    for legacy in (
        "ProviderCallBudget",
        "remaining_active_budget_seconds",
        "AGENT_TASK_TIMEOUT",
        "AGENT_EXECUTION_TIMEOUT",
        "AGENT_ITERATION_TIMEOUT",
        "AGENT_INFERENCE_TIMEOUT",
        "AGENT_TOOL_TIMEOUT",
        "AGENT_CONTEXT_TIMEOUT",
        "CAPABILITY_TIMEOUT",
        "GATEWAY_RESPONSE_TIMEOUT",
    ):
        assert legacy in text

    assert "T-0 itself introduces no runtime alias" in text
    assert "No row authorizes deletion, renaming, a new public error code" in text


def test_ubq5_t0_freezes_server_and_client_compatibility_owner_inventory() -> None:
    text = _doc()

    for path in (
        "se/src/runtimes/agent/runtime.py",
        "se/src/runtimes/workflow/runtime.py",
        "se/src/runtimes/agent/adapters/tool.py",
        "se/src/runtimes/agent/tool_execution/errors.py",
        "se/src/runtimes/capability/runtime.py",
        "se/src/transport/gateway/api/v1/chat_router.py",
        "cl/src/schemas/agent_execution.py",
        "cl/src/schemas/request.py",
        "cl/src/core/agent_engine.py",
        "cl/src/core/tool_executor.py",
        "cl/src/mcp_client/mcp_adapter.py",
        "cl/src/ui/bridge.py",
    ):
        assert path in text

    assert "client paths do not acquire timeout ownership" in text.lower()

def test_ubq5_t0_freezes_hidden_provider_timeout_owner_inventory() -> None:
    embedding = _read("se/src/provider/handlers/embedding_handler.py")
    provider_runtime = _read("se/src/runtimes/provider/runtime.py")
    main = _read("se/src/main.py")
    text = _doc()

    assert "call_budget = self._new_call_budget()" in embedding
    assert "self._remaining_timeout(call_budget)" in embedding
    assert '"timeout": context.config.provider.timeout' in provider_runtime
    assert "httpx.AsyncClient(timeout=config.provider.timeout)" in main

    for path in (
        "se/src/provider/handlers/embedding_handler.py",
        "se/src/runtimes/provider/runtime.py",
        "se/src/main.py",
    ):
        assert path in text

    assert "IN-SCOPE-LATER" in text
    assert "not a second logical-call deadline authority" in text


def test_ubq5_t0_freezes_remote_capability_timeout_and_reconciliation_boundaries() -> None:
    remote_driver = _read(
        "se/src/runtimes/capability/drivers/remote_client_driver.py"
    )
    realtime = _read("se/src/runtimes/connection/realtime.py")
    capability_router = _read(
        "se/src/transport/gateway/api/v1/capability_router.py"
    )
    resume_planning = _read("se/src/runtimes/agent/resume_planning.py")
    text = _doc()

    assert "timeout = context.remaining_seconds" in remote_driver
    assert "return await self._realtime.invoke(" in remote_driver
    assert "timeout=timeout" in remote_driver

    assert "Realtime invocation timeout must be > 0" in realtime
    assert "Realtime reconciliation timeout must be > 0" in realtime
    assert "Reconciliation timeout only abandons the query" in realtime
    assert "timeout_seconds=body.timeout_seconds" in capability_router

    assert "RECONCILIATION_UNAVAILABLE" in resume_planning
    assert "Remote reconciliation could not complete" in resume_planning

    for path in (
        "se/src/runtimes/capability/drivers/remote_client_driver.py",
        "se/src/runtimes/connection/realtime.py",
        "se/src/transport/gateway/api/v1/capability_router.py",
        "se/src/runtimes/agent/resume_planning.py",
    ):
        assert path in text

    normalized = _normalized_doc()
    assert "timeout after dispatch does not prove absence of side effects" in normalized
    assert "reconciliation timeout is query-only" in normalized
    assert "reconciliation_unavailable" in normalized


def test_ubq5_t0_keeps_terminal_timeout_distinct_from_future_tool_taxonomy() -> None:
    terminal = _read("tools/v1/terminal_tool.py")
    runtime = _read("se/src/runtimes/agent/runtime.py")
    text = _doc()

    assert '"TERMINAL_TIMEOUT"' in terminal
    assert '{"CAPABILITY_TIMEOUT", "TERMINAL_TIMEOUT"}' in runtime
    assert "TERMINAL_TIMEOUT" in text
    assert "COMPATIBILITY-KEEP" in text
    assert "does not alias or collapse it into `TOOL_CALL_TIMEOUT`" in text


def test_ubq5_t0_marks_adjacent_gateway_and_agent_timers_keep_or_out_of_scope() -> None:
    files_router = _read("se/src/transport/gateway/api/v1/files_router.py")
    models_router = _read("se/src/transport/gateway/api/v1/models_router.py")
    embeddings_router = _read(
        "se/src/transport/gateway/api/v1/embeddings_router.py"
    )
    coordinator = _read("se/src/runtimes/agent/coordinator.py")
    scanner = _read("se/src/runtimes/agent/stale_lease_scanner.py")
    chat_router = _read("se/src/transport/gateway/api/v1/chat_router.py")
    text = _doc()

    for router in (files_router, models_router, embeddings_router):
        assert "asyncio.wait_for" in router
        assert "config.provider.timeout" in router
        assert "asyncio.TimeoutError" in router

    assert "execution_limits.timeout_seconds" in coordinator
    assert "remaining_duration" in scanner
    assert "timeout=remaining_duration" in scanner
    assert "queue.get(), timeout=1.0" in chat_router
    assert 'yield ": ping\\n\\n"' in chat_router

    for path in (
        "se/src/transport/gateway/api/v1/files_router.py",
        "se/src/transport/gateway/api/v1/models_router.py",
        "se/src/transport/gateway/api/v1/embeddings_router.py",
        "se/src/runtimes/agent/coordinator.py",
        "se/src/runtimes/agent/stale_lease_scanner.py",
        "se/src/transport/gateway/api/v1/chat_router.py",
    ):
        assert path in text

    assert "OUT-OF-SCOPE-INTERNAL" in text
    normalized = _normalized_doc()
    assert "not semantic `response_idle_timeout` evidence" in normalized
    assert "stale-lease scanner duration remains r12 internal maintenance authority" in normalized


def test_ubq5_t0_inventory_classes_do_not_expand_production_authority() -> None:
    text = _doc()

    for classification in (
        "IN-SCOPE-LATER",
        "COMPATIBILITY-KEEP",
        "OUT-OF-SCOPE-INTERNAL",
    ):
        assert classification in text

    assert "production/runtime/schema/migration delta MUST remain ZERO" in text
    assert "T-0 changes nothing there" in text
    assert "production CLAIM = NONE" in text

