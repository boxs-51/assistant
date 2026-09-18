import cl.src.schemas.attachment as attachment_schemas
from cl.src.schemas.enums import MessageContentType
from cl.src.schemas.message import MessageContentPart
from cl.src.schemas.response import GatewayResponse


def test_gateway_response_defaults_are_constructible() -> None:
    response = GatewayResponse(model="test-model")

    assert response.metadata.provider == "unknown"


def test_message_content_part_accepts_explicit_empty_payload() -> None:
    part = MessageContentPart(type=MessageContentType.TEXT, text=None, data=None)

    assert part.text is None
    assert part.data is None


def test_text_content_type_is_removed_from_client_schema() -> None:
    assert not hasattr(attachment_schemas, "TextContent")


def test_client_migrates_legacy_text_content_payload() -> None:
    part = MessageContentPart.model_validate(
        {
            "type": "text",
            "data": {
                "data": "legacy text",
                "format": "structured",
            },
        }
    )

    assert part.text == "legacy text"
    assert part.data is None


def test_client_migrates_legacy_code_content_to_flat_markdown() -> None:
    part = MessageContentPart.model_validate(
        {
            "type": "text",
            "data": {
                "data": "print(1)",
                "format": "code",
                "language": "python",
            },
        }
    )

    assert part.text == "```python\nprint(1)\n```"
    assert part.data is None