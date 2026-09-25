from __future__ import annotations

import asyncio
from io import BytesIO

import httpx
import pytest

from se.src.infrastructure.config.schemas import ProviderConfig
from se.src.provider.core.interfaces.file import (
    ProviderUploadOutcome,
    ProviderUploadOutcomeKind,
)
from se.src.provider.gemini import GeminiProvider
from se.src.provider.mock import MockProvider, MockScenario


class _GeminiOutcomeClient:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        call_number = len(self.calls)

        if call_number == 1:
            if self.mode == "timeout_on_start":
                raise httpx.ReadTimeout(
                    "ambiguous timeout",
                    request=httpx.Request(method, url),
                )
            if self.mode == "cancel_on_start":
                raise asyncio.CancelledError()
            if self.mode == "safe_string_after_remote":
                raise RuntimeError("SAFE_NO_REMOTE_COMMIT")
            return httpx.Response(
                200,
                headers={
                    "X-Goog-Upload-URL": "https://upload.example/session/f5b"
                },
                request=httpx.Request(method, url),
            )

        if self.mode == "timeout_on_finalize":
            raise httpx.ReadTimeout(
                "finalize timeout",
                request=httpx.Request(method, url),
            )

        body = await _collect_async_content(kwargs["content"])
        assert body == b"hello"

        if self.mode == "missing_stable_id":
            payload = {
                "file": {
                    "displayName": "hello.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": "5",
                    "uri": "https://provider.invalid/unknown",
                }
            }
        else:
            payload = {
                "file": {
                    "name": "files/f5b-known",
                    "displayName": "hello.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": "5",
                    "uri": "https://provider.invalid/files/f5b-known",
                }
            }

        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request(method, url),
        )


class _NeverCalledClient:
    def __init__(self) -> None:
        self.calls = 0

    async def request(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("remote request must not be attempted")


async def _collect_async_content(content) -> bytes:
    chunks = []
    async for chunk in content:
        chunks.append(chunk)
    return b"".join(chunks)


def _gemini() -> GeminiProvider:
    return GeminiProvider(
        ProviderConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com",
        )
    )


def _upload_kwargs(client) -> dict:
    return {
        "http_client": client,
        "file_stream": BytesIO(b"hello"),
        "file_size": 5,
        "mime_type": "text/plain",
        "display_name": "hello.txt",
        "timeout": 10,
    }


def test_f5b_typed_outcome_requires_stable_identity_for_success():
    with pytest.raises(ValueError, match="stable provider_file_id"):
        ProviderUploadOutcome.remote_success_known(provider_file_id="")

    with pytest.raises(ValueError, match="cannot claim"):
        ProviderUploadOutcome(
            kind=ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN,
            provider_file_id="files/not-authoritative",
        )


@pytest.mark.asyncio
async def test_f5b_local_validation_failure_is_safe_before_remote_attempt():
    provider = _gemini()
    client = _NeverCalledClient()

    outcome = await provider.files.upload_file_outcome(
        http_client=client,
        file_stream=BytesIO(b"hello"),
        file_size=0,
        mime_type="text/plain",
        display_name="hello.txt",
        timeout=10,
    )

    assert outcome.kind is ProviderUploadOutcomeKind.SAFE_NO_REMOTE_COMMIT
    assert outcome.provider_file_id is None
    assert outcome.metadata["phase"] == "local_validation"
    assert client.calls == 0


@pytest.mark.asyncio
async def test_f5b_remote_success_requires_and_returns_stable_provider_identity():
    provider = _gemini()
    client = _GeminiOutcomeClient("success")

    outcome = await provider.files.upload_file_outcome(**_upload_kwargs(client))

    assert outcome.kind is ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN
    assert outcome.provider_file_id == "files/f5b-known"
    assert outcome.provider_uri == "https://provider.invalid/files/f5b-known"
    assert len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "timeout_on_start",
        "cancel_on_start",
        "safe_string_after_remote",
        "timeout_on_finalize",
    ],
)
async def test_f5b_remote_attempt_ambiguity_is_unknown_without_retry(mode: str):
    provider = _gemini()
    client = _GeminiOutcomeClient(mode)

    outcome = await provider.files.upload_file_outcome(**_upload_kwargs(client))

    assert outcome.kind is ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN
    assert outcome.provider_file_id is None

    if mode in {
        "timeout_on_start",
        "cancel_on_start",
        "safe_string_after_remote",
    }:
        assert len(client.calls) == 1
    else:
        assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_f5b_successful_remote_mutation_with_unparseable_id_is_unknown():
    provider = _gemini()
    client = _GeminiOutcomeClient("missing_stable_id")

    outcome = await provider.files.upload_file_outcome(**_upload_kwargs(client))

    assert outcome.kind is ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN
    assert outcome.provider_file_id is None
    assert outcome.metadata["phase"] == "response_adaptation"
    assert len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        ProviderUploadOutcomeKind.SAFE_NO_REMOTE_COMMIT,
        ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN,
        ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN,
    ],
)
async def test_f5b_mock_provider_deterministically_exposes_all_outcomes(kind):
    provider = MockProvider(
        config=ProviderConfig(
            base_url="http://mock.invalid",
            options={"seed": "cas-f5-b"},
        ),
        scenario=MockScenario(file_upload_outcome=kind.value),
    )

    outcome = await provider.files.upload_file_outcome(
        file_stream=BytesIO(b"hello"),
        file_size=5,
        mime_type="text/plain",
        display_name="hello.txt",
    )

    assert outcome.kind is kind
    if kind is ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN:
        assert outcome.provider_file_id
        assert provider.snapshot()["files"]
    else:
        assert outcome.provider_file_id is None
        assert provider.snapshot()["files"] == {}
