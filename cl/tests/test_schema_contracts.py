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
