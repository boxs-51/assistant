import httpx
import pytest
from tests.e2e.conftest import run_test_live
pytestmark = pytest.mark.live

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not run_test_live(),
        reason="Set RUN_LIVE_TESTS to run live tests.",
    ),
]

@pytest.mark.asyncio
async def test_live_health_and_readiness(live_base_url, live_headers):
    async with httpx.AsyncClient(timeout=15) as client:
        health = await client.get(f"{live_base_url}/health", headers=live_headers)
        assert health.status_code == 200
        assert health.json().get("status") == "ok"

        ready = await client.get(f"{live_base_url}/ready", headers=live_headers)
        assert ready.status_code == 200
        assert ready.json().get("status") == "ready"


@pytest.mark.asyncio
async def test_live_chat_with_mock_provider(live_base_url, live_headers):
    payload = {
        "model": "mock-chat",
        "provider": "mock",
        "messages": [{"role": "user", "content": "phase0"}],
        "config": {"stream": False},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{live_base_url}/v1/chat/completions",
            json=payload,
            headers=live_headers,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "mock"
    assert body["choices"][0]["message"]["content"] == "mock:phase0"


@pytest.mark.asyncio
async def test_live_stream_with_mock_provider(live_base_url, live_headers):
    payload = {
        "model": "mock-chat",
        "provider": "mock",
        "messages": [{"role": "user", "content": "hello world"}],
        "config": {"stream": True},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST",
            f"{live_base_url}/v1/chat/completions",
            json=payload,
            headers=live_headers,
        ) as response:
            assert response.status_code == 200, await response.aread()
            body = ""
            async for chunk in response.aiter_text():
                body += chunk

    assert "mock:hello" in body
    assert "mock:world" in body
    assert "[DONE]" in body


@pytest.mark.asyncio
async def test_live_embeddings_with_mock_provider(live_base_url, live_headers):
    payload = {
        "model": "mock-embedding",
        "provider": "mock",
        "input": ["hello", "world"],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{live_base_url}/v1/embeddings",
            json=payload,
            headers=live_headers,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 2
    assert body["data"][0]["object"] == "embedding"
    assert len(body["data"][0]["embedding"]) == 8


@pytest.mark.asyncio
async def test_live_models_with_mock_provider(live_base_url, live_headers):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{live_base_url}/v1/models/",
            params={"provider_name": "mock"},
            headers=live_headers,
        )
    assert response.status_code == 200, response.text
    model_ids = {item["id"] for item in response.json()["data"]}
    assert {"mock-chat", "mock-embedding"}.issubset(model_ids)

@pytest.mark.asyncio
async def test_live_auth_rejects_missing_credentials(live_base_url):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{live_base_url}/v1/chat/completions",
            json={
                "model": "mock-chat",
                "messages": [{"role": "user", "content": "unauthorized"}],
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_live_file_round_trip_with_mock_provider(live_base_url, live_headers):
    async with httpx.AsyncClient(timeout=30) as client:
        upload = await client.post(
            f"{live_base_url}/v1/files/",
            params={"provider_name": "mock", "display_name": "phase0.txt"},
            files={"file": ("phase0.txt", b"phase0-live", "text/plain")},
            headers=live_headers,
        )
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["name"]

        metadata = await client.get(
            f"{live_base_url}/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "metadata"},
            headers=live_headers,
        )
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["display_name"] == "phase0.txt"

        download = await client.get(
            f"{live_base_url}/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "download"},
            headers=live_headers,
        )
        assert download.status_code == 200
        assert download.content == b"phase0-live"

        delete = await client.delete(
            f"{live_base_url}/v1/files/{file_id}",
            params={"provider_name": "mock"},
            headers=live_headers,
        )
        assert delete.status_code == 204
