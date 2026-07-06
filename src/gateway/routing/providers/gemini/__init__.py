import httpx
import structlog
import base64
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, AsyncGenerator, List, Optional

# 1. Import các thành phần đã được module hóa
from ..base import (
    BaseProvider, ApiType,
    ApiKeyInQuery, ApiTypeMapper,
    EndpointBuilder,
    ModelCapabilityManager, ProviderCapability,
    ModelMapper
)
from .adapter import GeminiAdapter, FileHelper # Adapter chuyên biệt

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk, ModelList, ModelInfo, ContextLimits, PricingInfo
from .utils import *
from .api.chat import ChatGemini
from .api.files import FileGemini
from .api.model import ModelGemini

logger = structlog.get_logger(__name__)

# 2. Định nghĩa các model mapping (có thể chuyển ra file config YAML)
GEMINI_MODEL_MAP = {
    # Gemini 2.5
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",

    # Alias
    "gpt-4o": "gemini-2.5-pro",
    "gpt-4o-mini": "gemini-2.5-flash",

    # Legacy
    "gemini-pro": "gemini-2.5-pro",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",

    "default" : "gemini-2.5-flash"
}

# Ánh xạ ApiType sang endpoint template của Gemini
GEMINI_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "v1beta/models/{model}:{action}",
    ApiType.MODELS: "v1beta/models",
    ApiType.MODEL: "v1beta/models/{model}",
    ApiType.EMBEDDINGS: "v1beta/models/{model}:{action}",
    ApiType.IMAGE_GENERATION: "v1/images:generate", # Giả định endpoint cho Imagen 2
    ApiType.TEXT_TO_SPEECH: "v1/text:synthesize", # Giả định endpoint cho Text-to-Speech
    ApiType.FILES : "v1beta/files", # Endpoint cho File API
}

class GeminiProvider(
    BaseProvider,
    ChatGemini,
    FileGemini,
    ModelGemini
    ):
    """Nhà cung cấp cho Gemini API, được lắp ráp từ các thành phần chuyên biệt."""
    def __init__(self):
        # 3. Lắp ráp các thành phần (Composition)
        super().__init__(
            provider_name="gemini",
            auth_strategy=ApiKeyInQuery(api_key=str(settings.gemini.api_key), key_name="key"),
            endpoint_builder=EndpointBuilder(base_url=str(settings.gemini.base_url)),
            adapter=GeminiAdapter(),
            api_mapper=ApiTypeMapper(api_map=GEMINI_API_MAP),
            model_mapper=ModelMapper(model_map=GEMINI_MODEL_MAP),
            capability_manager=ModelCapabilityManager(provider_name="gemini"), # Có thể tạo GeminiCapabilityManager riêng sau này
            provider_capabilities={
                ProviderCapability.BATCH_API, # Gemini hỗ trợ batch embeddings
                ProviderCapability.FINE_TUNING,
            }
        )
        self.DEFAULT_MODEL = "gemini-1.5-flash"

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(settings.gemini.api_key)


    async def embeddings(self, **kwargs) -> Dict[str, Any]:
        """Tạo embeddings cho văn bản bằng API của Gemini."""
        body = kwargs.get("body")
        # Gemini sử dụng model embedding riêng, không giống model chat
        embedding_model = "embedding-001"
        # Adapt request body
        adapted_body = self.adapter.adapt_embeddings_request({"model": embedding_model, **body})

        action = "embedContent"
        # Nếu là batch request, action sẽ khác
        if "requests" in adapted_body:
            action = "batchEmbedContents"

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.EMBEDDINGS,
            json=adapted_body,
            timeout=kwargs.get("timeout"),
            model=embedding_model,
            action=action
        )
        return await self.adapter.adapt_embeddings_response(response)

    async def image_generation(self, **kwargs) -> Dict[str, Any]:
        """Tạo hình ảnh từ văn bản bằng API của Gemini (Imagen)."""
        body = kwargs.get("body")
        # Chuyển đổi request sang định dạng của Gemini
        adapted_body = self.adapter.adapt_image_generation_request(body)

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.IMAGE_GENERATION,
            json=adapted_body,
            timeout=kwargs.get("timeout"),
        )
        # Chuyển đổi response về định dạng chuẩn của Gateway (giống OpenAI)
        return await self.adapter.adapt_image_generation_response(response)


    

    async def model_capabilities(self, **kwargs): raise NotImplementedError("Gemini capabilities are inferred from the 'models' endpoint, no separate capabilities endpoint exists.")

    async def image_edit(self, **kwargs): raise NotImplementedError
    async def image_variation(self, **kwargs): raise NotImplementedError
    async def speech_to_text(self, **kwargs) -> Any: raise NotImplementedError
    async def text_to_speech(self, **kwargs) -> Any: raise NotImplementedError
    async def video_generation(self, **kwargs) -> Any: raise NotImplementedError
    async def video_understanding(self, **kwargs) -> Any: raise NotImplementedError


        
    async def create_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def list_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def get_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def count_tokens(self, **kwargs) -> Any: raise NotImplementedError
    async def live(self, **kwargs) -> Any: raise NotImplementedError

    
    async def upload_file(self, **kwargs) -> Any:
        """
        Tải tệp kích thước lớn lên Gemini File API bằng cơ chế Resumable Upload
        để không bị tràn bộ nhớ (RAM) và bypass giới hạn 20MB inlineData.
        """
        file_path = kwargs.get("file_path")
        display_name = kwargs.get("display_name")
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not file_path:
            logger.error("Missing required parameter 'file_path' in upload request")
            raise ValueError("Parameter 'file_path' is required.")

        if not http_client:
            logger.error("Missing required parameter 'http_client' in upload request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chuyển đổi file_path thành đối tượng Path để thao tác dễ dàng
        path_obj = Path(file_path)
        if not path_obj.is_file():
            logger.error("File does not exist at path", file_path=str(file_path))
            raise FileNotFoundError(f"File not found at {file_path}")

        # 1. Xác định kích thước file và MIME type
        try:
            file_size = os.path.getsize(path_obj)
            mime_type = FileHelper.detect_mime_type(path_obj)
            resolved_display_name = display_name or path_obj.name
        except Exception as e:
            logger.error("Failed to detect file properties", file_path=str(path_obj), error=str(e))
            raise e

        # --- BƯỚC 1: KHỞI TẠO SESSION RESUMABLE UPLOAD ---
        # File metadata được gửi trong phần body
        file_metadata = {
            "file": {
                "displayName": resolved_display_name,
            }
        }

        # Bắt buộc thêm các Header định danh cấu trúc dữ liệu tải lên cho Google Resumable protocol
        init_headers = {
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": mime_type,
        }

        logger.info(
            "Initiating resumable upload session to Gemini",
            provider=self.name,
            file_name=resolved_display_name,
            file_size_bytes=file_size,
            mime_type=mime_type
        )

        try:
            # Gửi request POST khởi tạo session upload (sử dụng ApiType.FILES)
            # Note: endpoint của resumable upload thường là v1beta/files?uploadType=resumable
            init_response = await self.send(
                client=http_client,
                method="POST",
                api_type=ApiType.FILES,
                params={"uploadType": "resumable"}, # Thêm query param chỉ định kiểu resumable
                headers=init_headers,
                json=file_metadata,
                timeout=timeout,
            )

            # Trích xuất Upload URL đặc định từ Header 'Location'
            # Tùy thuộc vào việc hàm self.send của bạn trả về object httpx.Response hay dict:
            # Nếu là httpx/aiohttp Response: sử dụng init_response.headers.get("Location")
            upload_url = getattr(init_response, "headers", {}).get("Location")
            
            if not upload_url:
                # Fallback nếu hàm self.send của bạn parse json sẵn và đưa headers vào chỗ khác
                if isinstance(init_response, dict) and "upload_url" in init_response:
                    upload_url = init_response.get("upload_url")
                else:
                    raise ValueError("Could not find 'Location' header in Gemini response to start uploading data.")

            logger.info("Resumable upload session created successfully. Starting data transmission...")

            # --- BƯỚC 2: STREAM DỮ LIỆU BINARY LÊN UPLOAD URL ---
            # Đối với file lớn, mở file ở dạng 'rb' và truyền trực tiếp qua HTTP client.
            # Hầu hết các client như httpx, aiohttp hỗ trợ truyền một "file-like object" 
            # giúp tự động stream dữ liệu theo từng block mà không nạp toàn bộ file vào RAM cùng lúc.
            
            with open(path_obj, "rb") as file_stream:
                # Gửi PUT request trực tiếp lên URL được chỉ định riêng từ Google
                # Note: Do gửi lên URL tùy biến (Location), hàm self.send của bạn cần hỗ trợ 
                # ghi đè endpoint đầy đủ nếu truyền trực tiếp url hoặc chỉnh lại logic.
                # Ở đây giả định bạn có thể request qua http_client bất đồng bộ gốc hoặc chỉnh phương thức send:
                
                upload_response = await http_client.request(
                    method="PUT",
                    url=upload_url,
                    content=file_stream, # Stream dữ liệu thô
                    headers={
                        "Content-Length": str(file_size),
                        "Content-Type": mime_type
                    },
                    timeout=timeout if timeout else 300.0 # Tăng timeout đối với file dung lượng lớn
                )

            logger.info(
                "Successfully completed big file upload process to Gemini",
                provider=self.name,
                status_code=upload_response.status_code
            )

            # 4. Ánh xạ dữ liệu trả về thông qua adapter
            return await self.adapter.adapt_file_upload_response(upload_response)

        except Exception as e:
            logger.error(
                "Unexpected error occurred during large file upload process",
                provider=self.name,
                file_name=resolved_display_name,
                error=str(e)
            )
            raise e



    async def batch_status(self, **kwargs) -> Any: raise NotImplementedError
    async def cancel_batch(self, **kwargs) -> Any: raise NotImplementedError
    async def list_batches(self, **kwargs) -> Any: raise NotImplementedError
    async def fine_tune(self, **kwargs) -> Any: raise NotImplementedError
    async def list_fine_tunes(self, **kwargs) -> Any: raise NotImplementedError
    async def fine_tune_status(self, **kwargs) -> Any: raise NotImplementedError
    async def cancel_fine_tune(self, **kwargs) -> Any: raise NotImplementedError
    async def assistant(self, **kwargs) -> Any: raise NotImplementedError
    async def assistant_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    async def rerank(self, **kwargs) -> Any: raise NotImplementedError
    async def vision(self, **kwargs) -> Any: raise NotImplementedError
    async def computer_use(self, **kwargs) -> Any: raise NotImplementedError
    async def browser(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    # ... and so on for all other abstract methods defined in BaseProvider.