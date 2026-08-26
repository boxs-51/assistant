import time
import json
import structlog
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Request, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from opentelemetry import trace

from ....domain.schemas import GatewayChatRequest, GatewayResponse, FinishReason
from ....domain.schemas.identity import Identity
from ..authentication.dependency import get_current_identity
from ..dependencies import get_container
from ....application.container import ApplicationContainer
from ....provider.exceptions import NoAvailableProviderError

router = APIRouter(tags=["LLM APIs"])
tracer = trace.get_tracer(__name__)
logger = structlog.get_logger(__name__)


async def parse_and_validate_request(request: Request) -> GatewayChatRequest:
    """1. Đọc JSON thô và ép sang schema Pydantic."""
    try:
        raw_body = await request.json()
    except Exception:
        logger.error("Invalid JSON format in request body")
        raise HTTPException(status_code=400, detail="Malformed JSON in request body.")

    try:
        return GatewayChatRequest(**raw_body)
    except ValidationError as val_err:
        logger.warning("Request schema validation failed", errors=val_err.errors())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Cấu trúc request hoặc messages không hợp lệ với hệ thống Multimodal Gateway.",
                "errors": val_err.errors(),
            },
        )


async def generate_stream_response(
    chat_request: GatewayChatRequest,
    identity: Identity,
    start_time: float,
    container: ApplicationContainer,
) -> AsyncGenerator[str, None]:
    """
    Generator xử lý luồng dữ liệu Streaming trực tiếp từ LLM Router.
    Lưu ý: Logic gọi Tool tạm thời được loại bỏ cho đến khi kết nối Capability Runtime.
    """
    body_dict = chat_request.model_dump(exclude_none=True)

    try:
        legacy_router = container.require("legacy_model_router")
        http_client = container.require("http_client")

        stream_chunks = legacy_router.stream_with_fallback(
            http_client=http_client, body=body_dict
        )

        detected_provider = None
        detected_model = None

        async for chunk in stream_chunks:
            # Lưu vết provider/model từ chunk nếu có
            if hasattr(chunk, "provider") and chunk.provider:
                detected_provider = chunk.provider
            if hasattr(chunk, "model") and chunk.model:
                detected_model = chunk.model

            # Bắn thẳng chunk dạng Server-Sent Events (SSE) về Client
            yield chunk.to_sse()

        # Kết thúc stream thành công
        latency = time.time() - start_time

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error("Error occurred during LLM streaming", error=str(e))
        yield f"data: {json.dumps({'error': 'An error occurred during response generation.'})}\n\n"
        yield "data: [DONE]\n\n"


async def handle_non_stream_response(
    chat_request: GatewayChatRequest,
    identity: Identity,
    start_time: float,
    container: ApplicationContainer,
) -> GatewayResponse:
    """
    Hàm xử lý dữ liệu Non-Streaming (Sync) trực tiếp từ LLM Router.
    """
    body_dict = chat_request.model_dump(exclude_none=True)

    legacy_router = container.require("legacy_model_router")
    http_client = container.require("http_client")

    # 1. Gọi trực tiếp Model Router xử lý
    gateway_response: GatewayResponse = await legacy_router.execute_with_fallback(
        http_client=http_client, body=body_dict
    )

    # 2. Xử lý Output Sanitization (Lọc nội dung nếu có config)
    choice = gateway_response.choices[0]
    message = choice.message

    with tracer.start_as_current_span("response_processing"):
        final_content = ""
        if isinstance(message.content, str):
            final_content = message.content
        elif isinstance(message.content, list):
            final_content = " ".join(
                part.data.data
                for part in message.content
                if part.type == "text" and hasattr(part.data, "data")
            )

        # Sử dụng output_filter lấy từ container
        output_filter = container.get("output_filter")
        if output_filter:
            safe_content = output_filter.sanitize(final_content)
            gateway_response.choices[0].message.content = safe_content

        latency = time.time() - start_time

        return gateway_response


@router.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """Endpoint proxy mỏng xử lý Request chat completions."""
    start_time = time.time()

    # 1. Validation request
    chat_request = await parse_and_validate_request(request)

    # 2. Điều hướng theo Stream hoặc Non-Stream
    try:
        if chat_request.config and chat_request.config.stream:
            return StreamingResponse(
                generate_stream_response(
                    chat_request, identity, start_time, container
                ),
                media_type="text/event-stream",
            )
        else:
            return await handle_non_stream_response(
                chat_request, identity, start_time, container
            )

    except NoAvailableProviderError as e:
        trace.get_current_span().record_exception(e)
        logger.critical("All LLM providers are unavailable", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service Unavailable: All LLM providers are currently down.",
        )
    except Exception as e:
        trace.get_current_span().record_exception(e)
        logger.error("Unhandled error in chat completions proxy", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Gateway Error",
        )