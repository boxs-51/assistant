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

class GeminiProvider(BaseProvider):
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

    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body")
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model") # Lấy model đã được dịch

        action = "generateContent"

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
            model=provider_model,
            action=action
        )
        return await self.adapter.adapt_chat_response(response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body")
        prepared_body = self.prepare_request(body)
        provider_model = prepared_body.get("model")

        action = "streamGenerateContent"

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
            model=provider_model,
            action=action
        )
        async for chunk in self.adapter.adapt_chat_stream(response):
            yield chunk

    def _map_to_model_info(self, gemini_raw_data: Dict[str, Any], fallback_id: str = "") -> ModelInfo:
        """Phương thức dùng chung để map dữ liệu thô từ Gemini API sang ModelInfo DTO."""
        raw_name = gemini_raw_data.get("name", "")  # e.g., "models/gemini-2.5-flash"
        model_id = raw_name.replace("models/", "") if raw_name else fallback_id

        if not model_id:
            raise ValueError("Model entry missing valid 'name' attribute and no fallback provided.")

        # 1. Trích xuất thông tin giới hạn (Context Limits)
        input_limit = gemini_raw_data.get("inputTokenLimit", 32768)
        output_limit = gemini_raw_data.get("outputTokenLimit", 8192)
        
        limits_dto = ContextLimits(
            context_window=input_limit,
            max_input_tokens=input_limit,
            max_output_tokens=output_limit
        )
        
        # 2. Tạo Pricing mặc định (xử lý tĩnh ở tầng trên)
        pricing_dto = PricingInfo()

        # 3. Tạo Object ModelInfo hoàn chỉnh
        return ModelInfo(
            id=model_id,
            display_name=gemini_raw_data.get("displayName", model_id),
            provider=self.name,
            family=model_id.split("-")[0] if "-" in model_id else model_id,
            version=gemini_raw_data.get("version", "v1"),
            description=gemini_raw_data.get("description", ""),
            limits=limits_dto,
            pricing=pricing_dto,
            capabilities=set(),  # KHÔNG gọi capability_manager ở đây để tránh vòng lặp vô hạn
            owned_by="google",
            metadata={
                "supported_generation_methods": gemini_raw_data.get("supportedGenerationMethods", []),
                "top_p": gemini_raw_data.get("topP"),
                "top_k": gemini_raw_data.get("topK"),
                "temperature": gemini_raw_data.get("temperature"),
            }
        )
    
    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model trực tiếp từ Gemini API và ánh xạ sang định dạng Gateway."""         
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        
        logger.info("Fetching raw models list from Gemini API", provider=self.name)
        
        try:
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=ApiType.MODELS,
                timeout=timeout,
            )
            gemini_data = response.json() 
            
            logger.debug(
                "Successfully received response from Gemini models endpoint", 
                status_code=response.status_code,
                response_keys=list(gemini_data.keys()) if isinstance(gemini_data, dict) else "Not a dict"
            )
        except Exception as e:
            logger.error("Failed to fetch or parse models from Gemini API", error=str(e), provider=self.name)
            raise e

        gemini_models = gemini_data.get("models", [])
        logger.info("Found raw models in Gemini response, starting mapping", count=len(gemini_models))

        final_models: List[ModelInfo] = []
        
        for m in gemini_models:
            try:
                # Gọi hàm map chung
                model_info = self._map_to_model_info(gemini_raw_data=m)
                final_models.append(model_info)
            except Exception as e:
                logger.error(
                    "Error mapping raw Gemini model data to ModelInfo DTO", 
                    model_id=m.get("name"), 
                    error=str(e)
                )
                continue

        logger.info(
            "Successfully mapped Gemini models list to Gateway DTOs", 
            raw_count=len(gemini_models), 
            mapped_count=len(final_models)
        )
        
        return ModelList(data=final_models)

    async def model(self, **kwargs) -> ModelInfo:
        """
        Lấy thông tin cấu hình chi tiết của một model cụ thể từ Gemini API.
        Chỉ thực hiện fetch và parse dữ liệu thô thông qua hàm map chung.
        """
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        raw_model_name = kwargs.get("model")

        if not raw_model_name:
            logger.error("Missing required parameter 'model_name' in model details request")
            raise ValueError("Parameter 'model_name' is required.")

        # Chuẩn hóa tên model: loại bỏ "models/" nếu có để dựng endpoint
        clean_model_name = raw_model_name.replace("models/", "")

        logger.info(
            "Requesting specific model metadata from Gemini",
            provider=self.name,
            requested_model=raw_model_name,
            resolved_endpoint=f"v1beta/models/{clean_model_name}"
        )

        try:
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=ApiType.MODEL, 
                model=clean_model_name,
                timeout=timeout
            )
            
            gemini_raw_data = response.json()
            
            logger.info(
                "Successfully fetched model metadata",
                provider=self.name,
                model_id=gemini_raw_data.get("name"),
                display_name=gemini_raw_data.get("displayName")
            )
            
            # Gọi hàm map chung, truyền thêm clean_model_name phòng trường hợp payload không trả về field 'name'
            return self._map_to_model_info(gemini_raw_data=gemini_raw_data, fallback_id=clean_model_name)

        except Exception as e:
            logger.error(
                "Unexpected error occurred while fetching model details",
                provider=self.name,
                model_name=raw_model_name,
                error=str(e)
            )
            raise e
        
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

    # =======================================================================
    # Placeholder implementations for abstract methods from BaseProvider
    # =======================================================================

    async def chat_batch(self, **kwargs): raise NotImplementedError
    

    async def model_capabilities(self, **kwargs): raise NotImplementedError("Gemini capabilities are inferred from the 'models' endpoint, no separate capabilities endpoint exists.")
    async def embeddings_batch(self, **kwargs): raise NotImplementedError
    async def image_edit(self, **kwargs): raise NotImplementedError
    async def image_variation(self, **kwargs): raise NotImplementedError
    async def audio(self, **kwargs): raise NotImplementedError
    async def speech_to_text(self, **kwargs) -> Any: raise NotImplementedError
    async def speech_to_text_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def text_to_speech(self, **kwargs) -> Any: raise NotImplementedError
    async def text_to_speech_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def audio_translation(self, **kwargs) -> Any: raise NotImplementedError
    async def video_generation(self, **kwargs) -> Any: raise NotImplementedError
    async def video_understanding(self, **kwargs) -> Any: raise NotImplementedError

    async def download_file(self, **kwargs) -> bytes:
        """
        Lưu ý quan trọng: Google Gemini File API hiện tại KHÔNG hỗ trợ tải ngược 
        nội dung nhị phân (binary content) của file về local sau khi đã upload.
        URI được trả về chỉ dùng làm tham chiếu ngữ cảnh (context reference) cho Model.
        """
        file_uri = kwargs.get("uri")
        logger.error(
            "Gemini File API does not support downloading binary data back after upload", 
            provider=self.name, 
            uri=file_uri
        )
        raise NotImplementedError(
            "Downloading binary contents via Gemini File API is not supported by Google. "
            "The file URI is purely intended for LLM context processing."
        )
        
    async def delete_file(self, **kwargs) -> bool:
        """Xóa một tệp khỏi hệ thống lưu trữ của Gemini File API."""
        raw_file_name = kwargs.get("file_name")
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not raw_file_name:
            logger.error("Missing required parameter 'file_name' in delete_file request")
            raise ValueError("Parameter 'file_name' is required.")
        if not http_client:
            logger.error("Missing required parameter 'http_client' in delete_file request")
            raise ValueError("Parameter 'http_client' is required.")

        clean_file_id = raw_file_name.replace("files/", "")

        logger.info(
            "Initiating file deletion request to Gemini",
            provider=self.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request DELETE tới v1beta/files/{clean_file_id}
            response = await self.send(
                client=http_client,
                method="DELETE",
                api_type=ApiType.FILES,
                model=clean_file_id,
                timeout=timeout
            )

            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            
            # Gemini trả về 200 OK và body rỗng khi xóa thành công
            # Thêm trường hợp nếu file không tồn tại (404), ta cũng coi như xóa thành công (Idempotent)
            if status_code in [200, 204]:
                logger.info("Successfully deleted file from Gemini", provider=self.name, file_id=clean_file_id)
                return True
            elif status_code == 404:
                logger.warning("File already deleted or expired on Gemini", provider=self.name, file_id=clean_file_id)
                return True
                
            return False

        except Exception as e:
            # Nếu hàm self.send tự ném lỗi khi gặp 404, hãy xử lý tại đây để tránh sập luồng vô lý
            if "404" in str(e):
                logger.warning("File not found during delete (might be expired)", provider=self.name, file_id=clean_file_id)
                return True
                
            logger.error("Unexpected error in delete_file", provider=self.name, file_id=clean_file_id, error=str(e))
            raise e
        
    async def list_files(self, **kwargs) -> List[Any]:
        """Lấy danh sách tất cả các tệp đã tải lên Gemini File API."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        page_size = kwargs.get("page_size")
        page_token = kwargs.get("page_token")

        if not http_client:
            logger.error("Missing required parameter 'http_client' in list_files request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chỉ nạp tham số nếu client thực sự truyền vào (tránh None)
        params = {}
        if page_size is not None:
            params["pageSize"] = int(page_size)
        if page_token:
            params["pageToken"] = str(page_token)

        logger.info("Fetching files list from Gemini File API", provider=self.name)

        try:
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=ApiType.FILES,
                params=params if params else None,
                timeout=timeout,
            )

            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            if status_code not in [200, 201]:
                logger.error("Failed to list files from Gemini", status_code=status_code)
                return []

            # Ánh xạ danh sách kết quả qua adapter
            return await self.adapter.adapt_file_list_response(response)

        except Exception as e:
            logger.error("Failed to fetch or parse files list from Gemini API", error=str(e), provider=self.name)
            raise e
        
    async def get_file(self, **kwargs) -> Any:
        """Lấy thông tin metadata của một tệp cụ thể từ Gemini File API."""
        raw_file_name = kwargs.get("file_name")  # Có thể là "files/abc" hoặc "abc"
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not raw_file_name:
            logger.error("Missing required parameter 'file_name' in get_file request")
            raise ValueError("Parameter 'file_name' is required.")
        if not http_client:
            logger.error("Missing required parameter 'http_client' in get_file request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chuẩn hóa tên file: loại bỏ tiền tố "files/" nếu có
        clean_file_id = raw_file_name.replace("files/", "")

        logger.info(
            "Fetching file metadata from Gemini",
            provider=self.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request GET tới v1beta/files/{clean_file_id}
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=ApiType.FILES,
                model=clean_file_id,  # Tận dụng tham số định tuyến id/name qua model/endpoint
                timeout=timeout
            )

            logger.info("Successfully fetched file metadata from Gemini", provider=self.name, file_id=clean_file_id)
            
            # Sử dụng lại hàm adapt_file_upload_response vì cấu trúc JSON trả về giống nhau
            return await self.adapter.adapt_file_upload_response(response)

        except Exception as e:
            logger.error("Unexpected error in get_file", provider=self.name, file_id=clean_file_id, error=str(e))
            raise e
    async def create_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def list_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def get_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def update_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def count_tokens(self, **kwargs) -> Any: raise NotImplementedError
    async def tokenize(self, **kwargs) -> Any: raise NotImplementedError
    async def detokenize(self, **kwargs) -> Any: raise NotImplementedError
    async def tool_call(self, **kwargs) -> Any: raise NotImplementedError
    async def execute_tool(self, **kwargs) -> Any: raise NotImplementedError
    async def web_search(self, **kwargs) -> Any: raise NotImplementedError
    async def url_context(self, **kwargs) -> Any: raise NotImplementedError
    async def execute_code(self, **kwargs) -> Any: raise NotImplementedError
    async def live(self, **kwargs) -> Any: raise NotImplementedError
    async def live_stream(self, **kwargs) -> Any: raise NotImplementedError 
    
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

    async def create_session(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_session(self, **kwargs) -> Any: raise NotImplementedError
    async def get_session(self, **kwargs) -> Any: raise NotImplementedError
    async def list_sessions(self, **kwargs) -> Any: raise NotImplementedError
    async def create_batch(self, **kwargs) -> Any: raise NotImplementedError
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
    async def ocr(self, **kwargs) -> Any: raise NotImplementedError
    async def computer_use(self, **kwargs) -> Any: raise NotImplementedError
    async def browser(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    # ... and so on for all other abstract methods defined in BaseProvider.