import pytest
from src.domain.schemas import ModelCapability
from src.provider.mock import MODEL_CAPABILITIES, MockProvider
from src.infrastructure.config.schemas import ProviderConfig

@pytest.mark.asyncio
@pytest.mark.parametrize("model,capability", [
    ("mock-chat", ModelCapability.CHAT),
    ("mock-embedding", ModelCapability.EMBEDDINGS),
    ("mock-vision", ModelCapability.VISION),
    ("mock-audio", ModelCapability.SPEECH_TO_TEXT),
    ("mock-image", ModelCapability.IMAGE_GENERATION),
    ("mock-video", ModelCapability.VIDEO_GENERATION),
    ("mock-batch", ModelCapability.CHAT_BATCH),
    ("mock-rerank", ModelCapability.RERANK),
])
async def test_capability_matrix(model, capability):
    provider=MockProvider(config=ProviderConfig(base_url="http://mock.invalid"))
    assert capability in MODEL_CAPABILITIES[model]
    assert await provider.has_capability(model, capability, None, 1) is True

@pytest.mark.asyncio
async def test_all_mock_capabilities_are_offline():
    p=MockProvider(config=ProviderConfig(base_url="http://mock.invalid"))
    assert (await p.audio.speech_to_text())["mock"]
    assert (await p.audio.text_to_speech(text="hello"))["mock"]
    assert (await p.audio.audio_translation())["mock"]
    assert (await p.vision.vision())["mock"]
    assert (await p.image.image_generation())["mock"]
    assert (await p.image.image_edit())["mock"]
    assert (await p.image.image_variation())["mock"]
    assert (await p.video.video_generation())["mock"]
    assert (await p.video.video_understanding())["mock"]
    bid=(await p.batch.create_batch(input=[]))["id"]
    assert (await p.batch.batch_status(batch_id=bid))["status"] == "completed"
    assert (await p.tokens.count_tokens(text="one two"))["total_tokens"] == 2
    assert (await p.reranking.rerank(documents=["a","b"]))["mock"]
    assert (await p.tooling.web_search(query="x"))["mock"]
    assert (await p.tooling.code_execution(code="x"))["mock"]
    assert (await p.moderation(input="x"))["mock"]
    assert (await p.computer_use(action="click"))["mock"]
