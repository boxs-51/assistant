from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Union

import httpx
import pytest
import pytest_asyncio

from tests.e2e.conftest import (run_file_test, run_heavy_test, run_embedding_test, run_test_live)
pytestmark = pytest.mark.live

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBEDDING_MODEL = os.getenv(
    "GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not run_test_live(),
        reason="Set RUN_LIVE_TESTS to run live tests.",
    ),
    pytest.mark.skipif(
        not GEMINI_API_KEY,
        reason="Set GEMINI_API_KEY to run live Gemini tests.",
    ),
]


@pytest_asyncio.fixture
async def client(live_base_url, live_headers, live_heavy_timeout):
    headers = {"Accept": "application/json"}
    if live_headers:
        headers["Authorization"] = live_headers["Authorization"]

    timeout = httpx.Timeout(
        connect=10.0,
        read=live_heavy_timeout,
        write=live_heavy_timeout,
        pool=10.0,
    )
    async with httpx.AsyncClient(
        base_url=live_base_url,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as http:
        yield http


def _json(response: httpx.Response, expected=(200,)) -> dict[str, Any]:
    assert response.status_code in expected, (
        f"{response.request.method} {response.request.url} -> "
        f"{response.status_code}: {response.text[:2000]}"
    )
    try:
        return response.json()
    except Exception as exc:
        raise AssertionError(
            f"Expected JSON from {response.request.url}: {response.text[:2000]}"
        ) from exc


def _text(payload: Union[Dict[str, Any], Any]) -> str:
    """
    Trích xuất toàn bộ nội dung văn bản từ GatewayResponse, GatewayStreamChunk
    hoặc Dictionary tương ứng theo đúng chuẩn DTO.
    """
    if not payload:
        return ""

    if isinstance(payload, dict):
        choices = payload.get("choices", [])
        if not choices or not isinstance(choices, list):
            return ""

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""

        # Dành cho Stream Chunk Dict
        if "delta" in first_choice:
            delta = first_choice.get("delta")
            return delta.get("content") or ""


        # Dành cho Non-stream Dict
        message = first_choice.get("message", {})
        return _extract_content_text(message.get("content"))

    return ""


def _extract_content_text(content: Union[List[Union[Dict[str, Any]]], str, None]) -> str:
    """Hàm phụ trợ trích xuất chuỗi từ content (dạng str hoặc List[MessageContentPart])."""
    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: List[str] = []
        for part in content:

            if part.get("text"):
                text_parts.append(str(part["text"]))
            else:
                data = part.get("data")
                if "data" in data and data["data"]:
                    text_parts.append(str(data["data"]))
                elif "extracted_text" in data and data["extracted_text"]:
                    text_parts.append(str(data["extracted_text"]))

        return "".join(text_parts)

    return str(content)


def _model_ids(payload: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in payload.get("data", []):
        if isinstance(item, dict) and item.get("id"):
            result.append(str(item["id"]))
    return result


async def _ready(client: httpx.AsyncClient):
    response = await client.get("/ready")
    _json(response)


@pytest.mark.asyncio
async def test_live_health_readiness_metrics_stats(client):
    started = time.perf_counter()

    assert client is not None
    _json(await client.get("/health"))
    _json(await client.get("/ready"))
    _json(await client.get("/stats"))

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.text

    print(f"\n[health] elapsed={time.perf_counter() - started:.3f}s")


@pytest.mark.asyncio
async def test_live_gemini_models_list_and_detail(client):
    await _ready(client)
    started = time.perf_counter()

    response = await client.get(
        "/v1/models/",
        params={"provider_name": "gemini"},
    )
    payload = _json(response)
    ids = _model_ids(payload)

    assert ids, f"Gateway returned an empty Gemini model list: {payload}"

    model_id = GEMINI_MODEL.replace("models/", "")
    detail = await client.get(
        f"/v1/models/{model_id}",
        params={"provider_name": "gemini"},
    )
    detail_payload = _json(detail)
    detail_id = str(detail_payload.get("id", model_id))

    assert model_id in detail_id or detail_id in model_id

    print(
        f"\n[models] count={len(ids)} model={model_id} "
        f"elapsed={time.perf_counter() - started:.3f}s"
    )


@pytest.mark.asyncio
async def test_live_gemini_chat_non_streaming(client, live_timeout):
    await _ready(client)

    request = {
        "model": GEMINI_MODEL,
        "provider": "gemini",
        "messages": [
            {"role": "user", "content": "Reply with exactly: LIVE_GEMINI_OK"}
        ],
        "config": {"stream": False},
    }

    started = time.perf_counter()
    response = await client.post(
        "/v1/chat/completions",
        json=request,
        timeout=live_timeout,
    )
    payload = _json(response)
    text = _text(payload)

    assert payload.get("provider") == "gemini"
    assert payload.get("model")
    assert "LIVE_GEMINI_OK" in text

    print(
        f"\n[chat] provider={payload.get('provider')} "
        f"model={payload.get('model')} chars={len(text)} "
        f"elapsed={time.perf_counter() - started:.3f}s"
    )


@pytest.mark.asyncio
async def test_live_gemini_chat_streaming(client, live_timeout):
    await _ready(client)

    request = {
        "model": GEMINI_MODEL,
        "provider": "gemini",
        "messages": [
            {"role": "user", "content": "Count from 1 to 5, one number per line."}
        ],
        "config": {"stream": True},
    }

    started = time.perf_counter()
    chunks: list[str] = []
    done = False

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json=request,
        timeout=live_timeout,
    ) as response:
        assert response.status_code == 200, (
            f"{response.status_code}: "
            f"{(await response.aread()).decode(errors='replace')[:4000]}"
        )

        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break

            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            for choice in obj.get("choices", []):
                piece = choice.get("delta", {}).get("content")
                if piece:
                    chunks.append(str(piece))

    output = "".join(chunks)
    assert done, "Streaming endpoint did not emit [DONE]"
    assert output.strip(), "Streaming endpoint returned no text"

    print(
        f"\n[stream] chunks={len(chunks)} chars={len(output)} "
        f"elapsed={time.perf_counter() - started:.3f}s"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not run_embedding_test,
    reason="Set LIVE_EMBEDDING_TEST=true to test Gemini Embedding API.",
)
async def test_live_gemini_embeddings(client, live_timeout):
    await _ready(client)

    request = {
        "model": GEMINI_EMBEDDING_MODEL,
        "provider": "gemini",
        "input": ["Gateway live Gemini embedding verification"],
    }

    started = time.perf_counter()
    response = await client.post(
        "/v1/embeddings",
        json=request,
        timeout=live_timeout,
    )
    payload = _json(response)

    data = payload.get("data")
    assert isinstance(data, list) and data
    embedding = data[0].get("embedding")

    assert isinstance(embedding, list) and embedding
    assert all(isinstance(x, (int, float)) for x in embedding)

    print(
        f"\n[embeddings] dimensions={len(embedding)} "
        f"elapsed={time.perf_counter() - started:.3f}s"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not run_file_test,
    reason="Set LIVE_FILE_TEST=true to test Gemini File API.",
)
async def test_live_gemini_file_upload_metadata_delete(client, live_heavy_timeout):
    await _ready(client)

    content = (
        b"AI Gateway live Gemini File API test.\n"
        b"Upload -> metadata -> delete.\n"
    )
    name = f"live-gemini-{int(time.time())}.txt"

    upload = await client.post(
        "/v1/files/",
        params={"provider_name": "gemini", "display_name": name},
        files={"file": (name, content, "text/plain")},
        timeout=live_heavy_timeout,
    )
    payload = _json(upload)
    file_id = payload.get("name") or payload.get("id")
    assert file_id, f"Upload response has no file id: {payload}"

    metadata = await client.get(
        f"/v1/files/{file_id}",
        params={"provider_name": "gemini", "action": "metadata"},
    )
    metadata_payload = _json(metadata)
    assert metadata_payload

    deleted = await client.delete(
        f"/v1/files/{file_id}",
        params={"provider_name": "gemini"},
    )
    assert deleted.status_code == 204, (
        f"Delete failed: {deleted.status_code} {deleted.text[:2000]}"
    )

    print(f"\n[files] uploaded={file_id} deleted=true")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not run_heavy_test,
    reason="Set LIVE_HEAVY_TEST=true to run heavy live tests.",
)
async def test_live_gemini_long_context(client, live_heavy_timeout, live_long_text_chars):
    await _ready(client)

    seed = (
        "This is a deterministic long-context integration test. "
        "The required marker is END_OF_LIVE_CONTEXT. "
    )
    repeats = max(1, live_long_text_chars // len(seed))
    long_text = (seed * repeats)[:live_long_text_chars]

    request = {
        "model": GEMINI_MODEL,
        "provider": "gemini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Read the complete user message and return only the exact "
                    "marker END_OF_LIVE_CONTEXT."
                ),
            },
            {
                "role": "user",
                "content": long_text + "\nEND_OF_LIVE_CONTEXT",
            },
        ],
        "config": {
            "stream": False,
            "max_output_tokens": 32,
        },
    }

    started = time.perf_counter()
    response = await client.post(
        "/v1/chat/completions",
        json=request,
        timeout=live_heavy_timeout,
    )
    payload = _json(response)
    output = _text(payload)

    assert "END_OF_LIVE_CONTEXT" in output

    print(
        f"\n[long-context] chars={len(long_text)} "
        f"elapsed={time.perf_counter() - started:.3f}s"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not run_heavy_test,
    reason="Set LIVE_HEAVY_TEST=true to run heavy live tests.",
)
async def test_live_gemini_concurrent_chat(client, live_heavy_timeout, live_concurrency):
    await _ready(client)

    async def call(index: int) -> tuple[int, str, float]:
        request = {
            "model": GEMINI_MODEL,
            "provider": "gemini",
            "messages": [
                {
                    "role": "user",
                    "content": f"Reply exactly with LIVE_CONCURRENCY_{index}",
                }
            ],
            "config": {"stream": False},
        }

        started = time.perf_counter()
        response = await client.post(
            "/v1/chat/completions",
            json=request,
            timeout=live_heavy_timeout,
        )
        payload = _json(response)
        return index, _text(payload), time.perf_counter() - started

    started = time.perf_counter()
    results = await asyncio.gather(
        *(call(i) for i in range(live_concurrency))
    )

    for index, output, elapsed in results:
        assert f"LIVE_CONCURRENCY_{index}" in output
        print(
            f"\n[concurrency] request={index} "
            f"elapsed={elapsed:.3f}s"
        )

    print(
        f"\n[concurrency] count={live_concurrency} "
        f"wall={time.perf_counter() - started:.3f}s"
    )
