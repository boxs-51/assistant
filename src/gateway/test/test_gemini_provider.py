import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

# Đường dẫn import chính xác từ hệ thống của bạn
from gateway.schemas import GatewayResponse, GatewayStreamChunk
from gateway.routing.providers.base.capability.capability import ProviderCapability
from gateway.routing.providers.gemini import GeminiProvider, GEMINI_MODEL_MAP
from src.gateway.config.core import ConfigurationRegistry


@pytest.fixture(autouse=True)
def mock_global_config():
    """
    Fixture tự động chạy (autouse=True) để inject cấu hình giả lập 
    vào thẳng ConfigurationRegistry của ứng dụng trước khi chạy bất kỳ test nào.
    """
    # Tạo mock cấu hình tổng
    mock_config_schema = MagicMock()
    
    # Tạo mock cấu hình riêng cho gemini
    mock_gemini = MagicMock()
    mock_gemini.api_key = "fake_api_key"
    mock_gemini.base_url = "https://generativelanguage.googleapis.com"
    
    # Gắn nhánh gemini vào cấu hình tổng
    mock_config_schema.gemini = mock_gemini
    
    # Ép Registry trả về mock_config_schema này
    old_config = ConfigurationRegistry._config
    ConfigurationRegistry._config = mock_config_schema
    
    yield mock_config_schema  # Trả về để các test khác có thể sử dụng hoặc chỉnh sửa nếu cần
    
    # Khôi phục lại trạng thái ban đầu sau khi test xong để tránh ảnh hưởng test khác
    ConfigurationRegistry._config = old_config


@pytest.fixture
def provider(mock_global_config):
    """Khởi tạo GeminiProvider với cấu hình đã được mock thành công."""
    return GeminiProvider()


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient phục vụ cho việc gửi request."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock()
    return client


# ==========================================
# TEST CASES
# ==========================================

def test_provider_initialization(provider):
    """Kiểm tra việc khởi tạo provider thành công với tên và các capabilities chính xác."""
    # SỬA TẠI ĐÂY: Đổi sang .name thay vì .provider_name theo thiết kế phổ biến của BaseProvider
    assert (getattr(provider, "name", None) or getattr(provider, "provider_name", None)) == "gemini"
    assert ProviderCapability.STREAMING in provider.capabilities
    assert ProviderCapability.TEXT_GENERATION in provider.capabilities


@pytest.mark.parametrize(
    "api_key_value, expected_result",
    [
        ("some_key_here", True),
        ("", False),
        (None, False),
    ]
)
def test_is_configured(mock_global_config, api_key_value, expected_result):
    """Kiểm tra method kiểm tra trạng thái cấu hình của API Key."""
    # Thay đổi giá trị api_key linh hoạt theo từng test case bằng cách ghi đè lên fixture config
    mock_global_config.gemini.api_key = api_key_value
    assert GeminiProvider.is_configured() is expected_result

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_model, expected_translated_model",
    [
        ("gpt-4o", "gemini-2.5-pro"),
        ("gemini-1.5-flash", "gemini-2.5-flash"),
        (None, "gemini-2.5-flash"),  # Trường hợp model mặc định (default)
    ]
)
async def test_request_non_streaming(provider, mock_http_client, input_model, expected_translated_model):
    """Kiểm tra logic tạo URL, map model, adapt body và gửi request đồng bộ (Non-Streaming)."""
    fake_body = {"model": input_model, "messages": [{"role": "user", "content": "Hi"}]} if input_model else {"messages": [{"role": "user", "content": "Hi"}]}
    fake_adapted_body = {"contents": [{"parts": [{"text": "Hi"}]}]}
    
    provider.adapter.adapt_request = MagicMock(return_value=fake_adapted_body)
    
    mock_response = MagicMock(spec=httpx.Response)
    mock_http_client.post.return_value = mock_response

    timeout = 30.0
    response = await provider.request(mock_http_client, body=fake_body, timeout=timeout)

    provider.adapter.adapt_request.assert_called_once_with(fake_body)
    
    mock_http_client.post.assert_called_once()
    called_url = mock_http_client.post.call_args[0][0]
    called_kwargs = mock_http_client.post.call_args[1]

    assert expected_translated_model in called_url
    assert "generateContent" in called_url
    assert "key=fake_api_key" in called_url 

    assert called_kwargs["json"] == fake_adapted_body
    assert called_kwargs["headers"]["Content-Type"] == "application/json"
    assert called_kwargs["timeout"] == timeout
    assert response == mock_response


@pytest.mark.asyncio
async def test_request_streaming(provider, mock_http_client):
    """Kiểm tra logic tạo request khi bật chế độ truyền dữ liệu luồng (stream: True)."""
    fake_body = {"model": "gemini-2.5-flash", "stream": True, "messages": []}
    provider.adapter.adapt_request = MagicMock(return_value={})
    
    mock_response = MagicMock(spec=httpx.Response)
    mock_http_client.post.return_value = mock_response

    await provider.request(mock_http_client, body=fake_body, timeout=10.0)

    called_url = mock_http_client.post.call_args[0][0]
    called_kwargs = mock_http_client.post.call_args[1]

    assert "streamGenerateContent" in called_url
    assert called_kwargs["headers"]["Accept"] == "text/event-stream"


@pytest.mark.asyncio
async def test_normalize_response_success(provider):
    """Kiểm tra normalize_response khi HTTP trả về thành công."""
    mock_response = MagicMock(spec=httpx.Response)
    
    # SỬA TẠI ĐÂY: Truyền dữ liệu bắt buộc (model) để thỏa mãn Pydantic validation
    expected_gateway_response = GatewayResponse(model="gemini-2.5-flash") 
    provider.adapter.adapt_response = AsyncMock(return_value=expected_gateway_response)

    result = await provider.normalize_response(mock_response)
    
    mock_response.raise_for_status.assert_called_once()
    provider.adapter.adapt_response.assert_called_once_with(mock_response)
    assert result == expected_gateway_response


@pytest.mark.asyncio
async def test_normalize_response_error(provider):
    """Kiểm tra normalize_response ném ra lỗi nếu HTTP status >= 400."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Bad Request", request=MagicMock(), response=mock_response
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider.normalize_response(mock_response)


@pytest.mark.asyncio
async def test_normalize_stream(provider):
    """Kiểm tra luồng xử lý và yielding các chunks trong hàm normalize_stream."""
    mock_response = MagicMock(spec=httpx.Response)
    
    # SỬA TẠI ĐÂY: Truyền dữ liệu bắt buộc (model, choices) để thỏa mãn Pydantic validation
    # (Nếu cấu trúc choices yêu cầu một list, hãy truyền một list rỗng [] hoặc mock)
    chunk1 = GatewayStreamChunk(model="gemini-2.5-flash", choices=[])
    chunk2 = GatewayStreamChunk(model="gemini-2.5-flash", choices=[])
    
    async def mock_generator():
        yield chunk1
        yield chunk2

    provider.adapter.adapt_stream = AsyncMock(return_value=mock_generator())

    generated_chunks = []
    async for chunk in provider.normalize_stream(mock_response):
        generated_chunks.append(chunk)

    mock_response.raise_for_status.assert_called_once()
    provider.adapter.adapt_stream.assert_called_once_with(mock_response)
    assert len(generated_chunks) == 2
    assert generated_chunks[0] == chunk1
    assert generated_chunks[1] == chunk2


# =================================================================
# KÍCH HOẠT CHẠY TRỰC TIẾP
# =================================================================
if __name__ == "__main__":
    import sys
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    sys.exit(pytest.main(["-v", "-s", __file__]))