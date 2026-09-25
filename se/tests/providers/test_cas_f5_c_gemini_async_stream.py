from __future__ import annotations

import httpx
import pytest

from se.src.infrastructure.config.schemas import ProviderConfig
from se.src.provider.core.interfaces.file import ProviderUploadOutcomeKind
from se.src.provider.gemini import GeminiProvider


class _StreamingClient:
    def __init__(self, *, fail_during_stream: bool = False) -> None:
        self.calls = []
        self.uploaded_chunks: list[bytes] = []
        self.fail_during_stream = fail_during_stream

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if len(self.calls) == 1:
            return httpx.Response(
                200,
                headers={
                    "X-Goog-Upload-URL":
                    "https://upload.example/session/f5c"
                },
                request=httpx.Request(method, url),
            )

        async for chunk in kwargs["content"]:
            self.uploaded_chunks.append(bytes(chunk))
            if self.fail_during_stream:
                raise RuntimeError("canonical stream failed after remote start")

        return httpx.Response(
            200,
            json={
                "file": {
                    "name": "files/f5c-stream",
                    "displayName": "stream.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": "5",
                    "uri": "https://provider.invalid/files/f5c-stream",
                }
            },
            request=httpx.Request(method, url),
        )


async def _canonical_object_stream():
    yield b"he"
    yield b"llo"


@pytest.mark.asyncio
async def test_f5c_gemini_outcome_boundary_accepts_async_object_stream():
    provider = GeminiProvider(
        ProviderConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com",
            file_binding_namespace="gemini-project-a",
        )
    )
    client = _StreamingClient()

    outcome = await provider.files.upload_file_outcome(
        http_client=client,
        file_stream=_canonical_object_stream(),
        file_size=5,
        mime_type="text/plain",
        display_name="stream.txt",
        timeout=10,
    )

    assert outcome.kind is ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN
    assert outcome.provider_file_id == "files/f5c-stream"
    assert client.uploaded_chunks == [b"he", b"llo"]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_f5c_stream_failure_after_remote_start_stays_unknown():
    provider = GeminiProvider(
        ProviderConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com",
            file_binding_namespace="gemini-project-a",
        )
    )
    client = _StreamingClient(fail_during_stream=True)

    outcome = await provider.files.upload_file_outcome(
        http_client=client,
        file_stream=_canonical_object_stream(),
        file_size=5,
        mime_type="text/plain",
        display_name="stream.txt",
        timeout=10,
    )

    assert outcome.kind is ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN
    assert outcome.provider_file_id is None
    assert len(client.calls) == 2
    assert client.uploaded_chunks == [b"he"]
