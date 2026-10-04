from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ubq5b_keeps_one_provider_call_budget_and_subordinate_fences() -> None:
    executor = _read("se/src/provider/executor.py")
    handler = _read("se/src/provider/handlers/chat_handler.py")
    retry = _read("se/src/provider/retry_contracts.py")

    assert "class ProviderCallBudget" in retry
    assert "first_response_timeout" in executor
    assert "stream_idle_timeout" in executor
    assert "call_budget.remaining_seconds" in executor
    assert "is_semantic_progress" in executor
    assert "_has_stream_semantic_or_finish_progression" in handler


def test_ubq5b_projects_timeout_truth_without_expanding_other_authority() -> None:
    exceptions = _read("se/src/provider/exceptions.py")
    provider_runtime = _read("se/src/runtimes/provider/runtime.py")
    agent_runtime = _read("se/src/runtimes/agent/runtime.py")
    chat = _read("se/src/provider/handlers/chat_handler.py")

    for code in (
        "PROVIDER_CALL_TIMEOUT",
        "PROVIDER_FIRST_RESPONSE_TIMEOUT",
        "PROVIDER_STREAM_IDLE_TIMEOUT",
    ):
        assert code in exceptions
    assert '"timeout_scope"' in provider_runtime
    assert '"timeout_seconds"' in provider_runtime
    assert "isinstance(timeout_error, ProviderTimeoutError)" in agent_runtime
    assert "_reserve_inference_quota" in chat
    assert "_settle_inference_quota_success" in chat


def test_ubq5b_preserves_released_r12_cas_and_unary_exclusions() -> None:
    chat = _read("se/src/provider/handlers/chat_handler.py")
    executor = _read("se/src/provider/executor.py")
    embedding = _read("se/src/provider/handlers/embedding_handler.py")
    adapter = _read("se/src/runtimes/agent/adapters/inference.py")

    assert "recovery_pre_attempt_guard" in chat
    assert "ProviderRecoveryGuardError" in chat
    assert "generated_asset_canonicalizer.canonicalize" in chat
    assert "provider_first_response_timeout_seconds" not in embedding
    assert "provider_stream_idle_timeout_seconds" not in embedding
    assert "deadline_monotonic" in adapter
    assert "ProviderCallBudget" in executor
