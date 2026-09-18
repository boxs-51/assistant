import asyncio

import pytest

from se.src.provider.gemini.converters.chats.request import RequestChats
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
)
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver


@pytest.mark.asyncio
async def test_python_driver_awaits_sync_handler_returning_task():
    """Regression for tools/v1/web_tool.run inside an active event loop."""

    async def async_result():
        await asyncio.sleep(0)
        return {"ok": True}

    def sync_wrapper(**kwargs):
        return asyncio.get_running_loop().create_task(async_result())

    driver = PythonCapabilityDriver(
        CapabilityDefinition(
            id="task.returning.tool",
            name="task.returning.tool",
            description="test",
            input_schema={"type": "object"},
        ),
        sync_wrapper,
    )

    result = await driver.execute(
        CapabilityExecutionContext.create(identity=None),
        {},
    )

    assert result == {"ok": True}
    assert not isinstance(result, asyncio.Future)


def test_gemini_request_extracts_nested_structured_text_and_never_emits_none():
    request = InferenceRequest(
        request_id="req-structured-tool-call",
        execution_id="exec-structured-tool-call",
        iteration=1,
        messages=[
            InferenceMessage(
                role="assistant",
                content=[
                    {
                        "type": "text",
                        "text": None,
                        "data": {
                            "data": "I will call the web tool.",
                            "format": "structured",
                            "encoding": None,
                        },
                    }
                ],
                tool_calls=[
                    {
                        "id": "call-web",
                        "name": "web_tool",
                        "arguments": {
                            "action": "search",
                            "query": "latest news",
                        },
                    }
                ],
            ),
        ],
        tools=[],
    )

    provider_body = ProviderInferenceAdapter.serialize_request(request)
    gemini_body = RequestChats().adapt_chat(provider_body)

    parts = gemini_body["contents"][0]["parts"]

    assert parts[0]["functionCall"]["name"] == "web_tool"
    assert parts[1] == {"text": "I will call the web tool."}
    assert all(
        part.get("text") is not None
        for part in parts
        if "text" in part
    )


def test_gemini_request_skips_text_part_when_both_text_and_data_are_missing():
    body = RequestChats().adapt_chat({
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": None, "data": None}],
                "tool_calls": [
                    {
                        "function": {
                            "name": "web_tool",
                            "arguments": {"action": "search", "query": "x"},
                        }
                    }
                ],
            }
        ]
    })

    assert body["contents"][0]["parts"] == [
        {
            "functionCall": {
                "name": "web_tool",
                "args": {"action": "search", "query": "x"},
            }
        }
    ]