from datetime import datetime, timezone

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.response import GatewayStreamChunk
from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.session.runtime import SessionRuntime
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def test_workflow_stream_text_preserves_plain_string():
    assert WorkflowRuntime._stream_text("hello") == "hello"


def test_workflow_stream_text_extracts_frozen_structured_message():
    message = InferenceMessage(
        role="assistant",
        content=[
            {
                "type": "text",
                "text": None,
                "data": {
                    "data": "Chào bạn! ",
                    "format": "structured",
                    "encoding": None,
                },
            },
            {
                "type": "text",
                "text": None,
                "data": {
                    "data": "Tôi có thể giúp gì cho bạn không?",
                    "format": "structured",
                    "encoding": None,
                },
            },
        ],
    )

    content = WorkflowRuntime._stream_text(message.content)

    assert content == "Chào bạn! Tôi có thể giúp gì cho bạn không?"
    assert isinstance(content, str)


def test_synthetic_stream_chunk_respects_gateway_stream_delta_contract():
    content = WorkflowRuntime._stream_text([
        {
            "type": "text",
            "data": {
                "data": "hello",
                "format": "structured",
            },
        }
    ])

    chunk = GatewayStreamChunk.model_validate({
        "id": "agent-1",
        "model": "mock",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
            }
        ],
    })

    assert chunk.choices[0].delta.content == "hello"


@pytest.mark.asyncio
async def test_session_stream_buffer_survives_failed_persist_for_retry():
    runtime = SessionRuntime()
    key = ("session-1", "turn-1")
    runtime._stream_buffers[key] = {
        "chunks": ["Hello ", "world"],
        "created_at": datetime.now(timezone.utc),
    }

    calls = 0

    async def persist(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary persistence failure")

    runtime._persist_assistant_message = persist
    event = BaseEvent(
        event_name="provider.stream.completed",
        session_id="session-1",
        turn_id="turn-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="temporary persistence failure"):
        await runtime._on_stream_completed(event)

    assert key in runtime._stream_buffers
    assert runtime._stream_buffers[key]["chunks"] == ["Hello ", "world"]

    await runtime._on_stream_completed(event)

    assert key not in runtime._stream_buffers
    assert calls == 2