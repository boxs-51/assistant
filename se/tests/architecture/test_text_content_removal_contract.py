import se.src.domain.schemas.attachment as attachment_schemas
from se.src.domain.schemas.message import MessageContentPart
from se.src.provider.gemini.converters.chats.request import RequestChats
from se.src.provider.gemini.converters.chats.response import ResponseChats


def test_text_content_type_is_removed_from_server_schema():
    assert not hasattr(attachment_schemas, "TextContent")


def test_legacy_structured_text_payload_migrates_to_flat_text():
    part = MessageContentPart.model_validate(
        {
            "type": "text",
            "data": {
                "data": "hello",
                "format": "structured",
                "encoding": None,
            },
        }
    )

    assert part.text == "hello"
    assert part.data is None


def test_legacy_code_payload_preserves_markdown_fence():
    part = MessageContentPart.model_validate(
        {
            "type": "text",
            "data": {
                "data": "print('hello')",
                "format": "code",
                "language": "python",
            },
        }
    )

    assert part.text == "```python\nprint('hello')\n```"
    assert part.data is None


def test_gemini_response_emits_flat_text_parts_only_for_text():
    converter = ResponseChats()

    parts = converter._parse_and_split_text_content(
        "before\n```python\nprint('hello')\n```\nafter"
    )

    assert [part.data for part in parts] == [None, None, None]
    assert parts[0].text == "before"
    assert parts[1].text == "```python\nprint('hello')\n```"
    assert parts[2].text == "after"


def test_gemini_thinking_uses_text_field_not_data():
    converter = ResponseChats()

    parts, tool_calls, reasoning = converter._parse_gemini_parts_to_content(
        [{"thought": True, "text": "checking sources"}]
    )

    assert tool_calls == []
    assert reasoning == "checking sources"
    assert len(parts) == 1
    assert parts[0].type == "thinking"
    assert parts[0].text == "checking sources"
    assert parts[0].data is None


def test_gemini_response_to_request_round_trip_has_no_nested_text_content():
    response_converter = ResponseChats()
    request_converter = RequestChats()

    parts = response_converter._parse_and_split_text_content(
        "answer\n```python\nprint(1)\n```"
    )
    content = [
        part.model_dump(mode="json", exclude_none=True)
        for part in parts
    ]

    body = request_converter.adapt_chat(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": content,
                }
            ]
        }
    )

    gemini_parts = body["contents"][0]["parts"]
    assert gemini_parts == [
        {"text": "answer"},
        {"text": "```python\nprint(1)\n```"},
    ]
    assert all(item.get("text") is not None for item in gemini_parts)