import httpx
import pytest

from src.infrastructure.config.schemas import ProviderConfig
from src.provider.gemini import GeminiProvider


class _FakeProviderClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))

        if len(self.calls) == 1:
            return httpx.Response(
                200,
                headers={
                    "X-Goog-Upload-URL": "https://upload.example/session/abc"
                },
                request=httpx.Request(method, url),
            )

        body = await _collect_async_content(kwargs["content"])
        assert body == b"hello"
        return httpx.Response(
            200,
            json={
                "file": {
                    "name": "files/abc",
                    "displayName": "hello.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": "5",
                    "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc",
                }
            },
            request=httpx.Request(method, url),
        )


async def _collect_async_content(content):
    chunks = []
    async for chunk in content:
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_gemini_resumable_upload_uses_x_goog_upload_url_and_protocol():
    provider = GeminiProvider(
        ProviderConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com",
        )
    )
    client = _FakeProviderClient()

    result = await provider.files.upload_file(
        http_client=client,
        file_stream=__import__("io").BytesIO(b"hello"),
        file_size=5,
        mime_type="text/plain",
        display_name="hello.txt",
        timeout=10,
    )

    init_method, init_url, init_kwargs = client.calls[0]
    headers = init_kwargs.get("headers", {})
    assert init_method == "POST"
    assert headers.get("x-goog-api-key") == "test-key"
    assert init_url == "https://generativelanguage.googleapis.com/upload/v1beta/files"
    assert init_kwargs["headers"]["X-Goog-Upload-Protocol"] == "resumable"
    assert init_kwargs["headers"]["X-Goog-Upload-Command"] == "start"
    assert init_kwargs["headers"]["X-Goog-Upload-Header-Content-Length"] == "5"
    assert init_kwargs["headers"]["X-Goog-Upload-Header-Content-Type"] == "text/plain"

    upload_method, upload_url, upload_kwargs = client.calls[1]
    assert upload_method == "POST"
    assert upload_url == "https://upload.example/session/abc"
    assert upload_kwargs["headers"]["X-Goog-Upload-Offset"] == "0"
    assert upload_kwargs["headers"]["X-Goog-Upload-Command"] == "upload, finalize"

    assert result.id == "abc"