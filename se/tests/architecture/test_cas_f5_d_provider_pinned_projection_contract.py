from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from se.src.domain.schemas.attachment import GatewayAttachment


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F5_D_PROVIDER_PINNED_TRANSIENT_PROJECTION_CONTRACT_912CF1AC.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def test_f5d_contract_freezes_attempt_scoped_provider_handoff_and_fallback():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    required = (
        "per-provider attempt after:",
        "capability eligibility has passed",
        "before ProviderExecutor mutates the remote provider",
        "request copy only",
        "REUSED",
        "HYDRATED",
        "HYDRATION_IN_PROGRESS",
        "HYDRATION_OUTCOME_UNKNOWN",
        "HYDRATION_FINGERPRINT_DRIFT",
        "HYDRATION_PERSISTENCE_CONFLICT",
        "A SAFE local hydration failure is not permission to try another provider.",
        "fallback-terminal for the logical inference",
        "entering the F5-D hydration/projection hook latches",
        "MUST NOT silently continue to the next provider",
        "MUST NOT hydrate/project the same canonical asset into a second provider",
        "Provider fallback remains ProviderRuntime/provider-handler authority **before**",
        "streaming",
    )
    for phrase in required:
        assert phrase in contract


def test_f5d_current_provider_authority_is_inside_the_eligible_attempt():
    source = _read("se/src/provider/handlers/chat_handler.py")

    routing = source.index("self.routing_policy.get_fallback_chain(")
    healthy = source.index("self._get_healthy_fallback_chain(", routing)
    loop = source.index("for provider in healthy_execution_chain:", healthy)
    capability = source.index(
        "if not await self._has_required_capabilities(",
        loop,
    )
    executor = source.index("return await self.executor.execute(", capability)

    assert routing < healthy < loop < capability < executor
    assert "provider=provider" in source[executor : executor + 400]

    # Current generic fallback still continues after provider errors. F5-D's
    # contract must therefore explicitly fence fallback once projection engages.
    fallback_tail = source[executor : source.index("async def stream_with_fallback")]
    assert "ProviderError," in fallback_tail
    assert "httpx.RequestError," in fallback_tail
    assert "httpx.HTTPStatusError," in fallback_tail
    assert "continue" in fallback_tail


def test_f5d_stream_provider_authority_is_inside_the_eligible_attempt():
    source = _read("se/src/provider/handlers/chat_handler.py")
    stream_start = source.index("async def stream_with_fallback")
    stream = source[stream_start:]

    routing = stream.index("self.routing_policy.get_fallback_chain(")
    healthy = stream.index("self._get_healthy_fallback_chain(", routing)
    loop = stream.index("for provider in healthy_execution_chain:", healthy)
    capability = stream.index(
        "if not await self._has_required_capabilities(",
        loop,
    )
    chat_stream = stream.index(
        "base_capability=ModelCapability.CHAT_STREAM",
        capability,
    )
    executor = stream.index("self.executor.execute_stream(", chat_stream)

    assert routing < healthy < loop < capability < chat_stream < executor
    assert "provider=provider" in stream[executor : executor + 400]

    # Current generic streaming behavior falls back only before visible output:
    # once a chunk is visible it raises, otherwise it records the error and
    # continues. F5-D's stronger rule must fence that continue after projection.
    provider_errors = stream.index("except (", executor)
    visible_guard = stream.index("if stream_started:", provider_errors)
    fallback_continue = stream.index("continue", visible_guard)

    assert executor < provider_errors < visible_guard < fallback_continue
    assert "raise detail from error" in stream[visible_guard:fallback_continue]
    assert "All providers failed before streaming output started." in stream


def test_f5d_direct_and_agent_share_one_provider_inference_boundary():
    direct = _read("se/src/runtimes/chat/direct.py")
    agent = _read("se/src/runtimes/agent/runtime.py")
    adapter = _read("se/src/runtimes/agent/adapters/inference.py")

    assert "self._inference.complete(InferenceRequest(" in direct
    assert "response = await self._inference.complete(" in agent
    assert "handler.execute_with_fallback(" in adapter

    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))
    assert "DIRECT and AGENT must not implement separate hydration algorithms." in contract
    assert "shared per-provider attempt boundary" in contract


def test_f5d_legacy_provider_event_converges_on_same_handler_and_guard_stays_closed():
    workflow = _read("se/src/runtimes/workflow/runtime.py")
    provider_runtime = _read("se/src/runtimes/provider/runtime.py")

    assert "ASSET_HYDRATION_REQUIRED" in workflow
    assert 'event_name="provider.chat.execute"' in workflow
    assert 'self.event_bus.subscribe("provider.chat.execute"' in provider_runtime
    assert "self.chat_handler.execute_with_fallback(" in provider_runtime

    contract = CONTRACT.read_text(encoding="utf-8")
    assert "WorkflowRuntime `ASSET_HYDRATION_REQUIRED` guard replacement" in contract
    assert "legacy `provider.chat.execute` event path" in contract


@pytest.mark.parametrize(
    "payload",
    [
        {
            "asset_id": "asset-f5d",
            "source": "asset",
            "mime_type": "application/pdf",
            "provider_file_id": "files/provider-copy",
        },
        {
            "asset_id": "asset-f5d",
            "source": "asset",
            "mime_type": "application/pdf",
            "uri": "https://provider.invalid/files/provider-copy",
        },
    ],
)
def test_f5d_canonical_asset_cannot_persist_provider_native_identity(payload):
    with pytest.raises((ValueError, ValidationError)):
        GatewayAttachment.model_validate(payload)

    contract = CONTRACT.read_text(encoding="utf-8")
    assert "persisted canonical `GatewayAttachment(asset_id=...)`" in contract
    assert "Provider identity is secondary execution state" in contract


def test_f5d_asset_hook_terminal_latch_covers_partial_multi_asset_failure():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    required = (
        "entering the F5-D hydration/projection hook latches that logical inference as fallback-terminal",
        "before the first asset hydration result is processed",
        "The latch MUST NOT be deferred until the complete transient request projection has been built.",
        "the first `REUSED` or `HYDRATED` asset result cannot weaken or defer this latch",
        "if asset A is reused/hydrated and any later asset B fails",
        "MUST fail closed on the same exact provider attempt",
        "MUST NOT restart hydration/projection on a different provider",
        "applies equally to stream and non-stream execution",
    )
    for phrase in required:
        assert phrase in contract


def test_f5d_gemini_missing_provider_uri_is_fail_closed_and_terminal():
    interface = _read("se/src/provider/core/interfaces/file.py")
    hydration = _read("se/src/application/assets/hydration.py")
    gemini = _read("se/src/provider/gemini/api/files.py")

    assert "provider_uri: str | None = None" in interface
    assert "provider_uri: Optional[str] = None" in hydration
    assert 'provider_uri=getattr(attachment, "uri", None)' in gemini

    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))
    required = (
        "provider-specific projection eligibility requires every provider-native field required by that provider",
        "for Gemini, `provider_uri` / `fileUri` MUST be a server-proven non-blank string",
        "with missing or blank `provider_uri` MUST fail closed before provider inference",
        "remains fallback-terminal under the F5-D hook latch",
        "MUST NOT be reconstructed from client metadata",
        "guessed from `provider_file_id`",
        "switching to another provider",
    )
    for phrase in required:
        assert phrase in contract

def test_f5d_gemini_native_projection_is_distinct_from_current_inline_reload_path():
    attachment = _read(
        "se/src/provider/gemini/converters/chats/acttachment.py"
    )
    request = _read(
        "se/src/provider/gemini/converters/chats/request.py"
    )
    current_request_projection = attachment + "\n" + request

    assert '"inlineData"' in current_request_projection
    assert "Path(" in current_request_projection
    assert "base64.b64encode" in current_request_projection
    assert '"fileData"' not in current_request_projection
    assert '"fileUri"' not in current_request_projection

    contract = CONTRACT.read_text(encoding="utf-8")
    assert '"fileData"' in contract
    assert '"fileUri"' in contract
    assert "no local-path reload" in contract
    assert "no base64 reload of an already hydrated provider file" in contract


def test_f5d_zero_delta_contract_keeps_destructive_and_cross_track_authority_closed():
    contract = CONTRACT.read_text(encoding="utf-8")

    closed = (
        "production hydration/projection wiring",
        "automatic UNKNOWN/stale PROCESSING recovery",
        "provider remote delete/reclamation",
        "READY FileAsset deletion/release",
        "FileBlob/CAS physical GC or reconciliation",
        "CTX search/read/Memory/Personalization authority",
        "R11 retention/destructive-GC authority",
        "R12 lease/recovery authority",
        "CAS-F6",
    )
    for phrase in closed:
        assert phrase in contract

    assert "Provider binding/provider identity MUST NOT become" in contract
    assert "Memory promotion authority" in contract


def test_f5d_contract_freezes_current_integration_baseline():
    contract = CONTRACT.read_text(encoding="utf-8")

    assert "canonical main = fd3c3a146f0775ce357b846341f99475af55c665" in contract
    assert "post-main Architecture #1337 = GREEN/GREEN" in contract


def test_f5d_contract_is_explicitly_preimplementation_only():
    contract = CONTRACT.read_text(encoding="utf-8")
    chat_handler = _read("se/src/provider/handlers/chat_handler.py")

    assert "production/runtime/schema/migration delta = 0" in contract
    assert "F5-D production implementation remains CLOSED" in contract
    assert "CanonicalAssetHydrationService" not in chat_handler
