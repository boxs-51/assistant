import pytest
from io import BytesIO
from src.domain.schemas import ModelCapability
from src.provider.mock import MockProvider

@pytest.mark.asyncio
async def test_chat_is_deterministic():
    p=MockProvider()
    body={"model":"mock-chat","messages":[{"role":"user","content":"hello world"}]}
    a=await p.chat.chat(body=body); b=await p.chat.chat(body=body)
    assert a.id == b.id
    assert a.choices[0].message.content == "mock:hello world"
    assert await p.has_capability("mock-chat", ModelCapability.CHAT, None, 1)

@pytest.mark.asyncio
async def test_embeddings_are_deterministic():
    p=MockProvider()
    body={"model":"mock-embedding","input":["hello"]}
    assert await p.embeddings.embeddings(body=body) == await p.embeddings.embeddings(body=body)

@pytest.mark.asyncio
async def test_files_round_trip_and_reset():
    p=MockProvider(seed="test")
    f=await p.files.upload_file(file_stream=BytesIO(b"hello"),file_size=5,mime_type="text/plain",display_name="a.txt")
    fid=f["name"]
    assert await p.files.download_file(file_id=fid) == b"hello"
    assert await p.files.delete_file(file_id=fid)
    p.reset()
    assert p.snapshot()["files"] == {}

@pytest.mark.asyncio
async def test_network_guard():
    p=MockProvider()
    with pytest.raises(Exception) as exc:
        await p.send(None,"whatever",timeout=1)
    assert "mock_network_forbidden" in str(exc.value.error_code)
