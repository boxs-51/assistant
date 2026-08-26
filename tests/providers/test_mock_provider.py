import pytest

from src.domain.schemas import ModelCapability
from src.provider.mock.provider import MockProvider, MOCK_MODEL, MOCK_EMBEDDING_MODEL


@pytest.mark.asyncio
async def test_mock_chat_is_deterministic_and_offline():
    provider = MockProvider()
    result = await provider.chat.chat(body={
        "model": MOCK_MODEL,
        "messages": [{"role": "user", "content": "hello world"}],
    })

    assert result.provider == "mock"
    assert result.model == MOCK_MODEL
    assert result.choices[0].message.content == "mock:hello world"
    assert await provider.has_capability(MOCK_MODEL, ModelCapability.CHAT, None, 1) is True
    assert await provider.has_capability(MOCK_MODEL, ModelCapability.CHAT_STREAM, None, 1) is True


@pytest.mark.asyncio
async def test_mock_stream_emits_chunks_without_network():
    provider = MockProvider()
    chunks = [chunk async for chunk in provider.chat.chat_stream(body={
        "model": MOCK_MODEL,
        "messages": [{"role": "user", "content": "one two"}],
    })]

    assert len(chunks) == 2
    assert "mock:one" in chunks[0].choices[0].delta.content
    assert chunks[-1].choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_mock_embeddings_are_stable():
    provider = MockProvider()
    first = await provider.embeddings.embeddings(body={
        "model": MOCK_EMBEDDING_MODEL,
        "input": ["hello"],
    })
    second = await provider.embeddings.embeddings(body={
        "model": MOCK_EMBEDDING_MODEL,
        "input": ["hello"],
    })

    assert first == second
    assert len(first["data"][0]["embedding"]) == 8
    assert await provider.has_capability(MOCK_EMBEDDING_MODEL, ModelCapability.EMBEDDINGS, None, 1) is True


@pytest.mark.asyncio
async def test_mock_files_round_trip():
    provider = MockProvider()
    uploaded = await provider.files.upload_file(
        file_stream=__import__("io").BytesIO(b"phase0"),
        file_size=6,
        mime_type="text/plain",
        display_name="phase0.txt",
    )
    file_id = uploaded["name"]

    meta = await provider.files.get_file(file_id=file_id)
    payload = await provider.files.download_file(file_id=file_id)
    assert meta["display_name"] == "phase0.txt"
    assert payload == b"phase0"
    assert await provider.files.delete_file(file_id=file_id) is True
