import httpx
import structlog
import base64
import asyncio
from typing import Dict, Any, AsyncGenerator, List, Optional

# 1. Import các thành phần đã được module hóa
from ..base.provider import BaseProvider
from ..base.api import ApiType
from ..base.auth import ApiKeyInQuery
from ..base.api_mapper import ApiTypeMapper
from ..base.endpoint import EndpointBuilder
from ..base.capability import ModelCapabilityManager, ProviderCapability
from ..base.model_mapper import ModelMapper
from .adapter import GeminiAdapter, FileHelper # Adapter chuyên biệt

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk, ModelList, ModelInfo, ContextLimits, PricingInfo

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
    ApiType.EMBEDDINGS: "v1beta/models/{model}:{action}",
    ApiType.IMAGE_GENERATION: "v1/images:generate", # Giả định endpoint cho Imagen 2
    ApiType.TEXT_TO_SPEECH: "v1/text:synthesize", # Giả định endpoint cho Text-to-Speech
    "upload_file": "v1beta/files", # Endpoint cho File API
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

        logger.info(f"Prepared request body: {prepared_body}, using provider model: {provider_model}")

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

        logger.info(f"Prepared request body: {prepared_body}, using provider model: {provider_model}")

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

    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model trực tiếp từ Gemini API và ánh xạ sang định dạng Gateway."""
        import asyncio
        
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

        # Gemini API trả về key 'models' thay vì 'data'
        gemini_models = gemini_data.get("models", [])
        logger.info("Found raw models in Gemini response, starting mapping", count=len(gemini_models))

        final_models: List[ModelInfo] = []
        
        for m in gemini_models:
            raw_name = m.get("name", "")  # e.g., "models/gemini-2.5-flash"
            model_id = raw_name.replace("models/", "") if "models/" in raw_name else raw_name
            
            if not model_id:
                logger.warning("Skipping model entry with empty or invalid name attribute", model_data=m)
                continue
            
            try:
                # 1. Trích xuất thông tin giới hạn (Context Limits) từ Gemini payload
                input_limit = m.get("inputTokenLimit", 32768)
                output_limit = m.get("outputTokenLimit", 8192)
                
                limits_dto = ContextLimits(
                    context_window=input_limit,       # Gemini sử dụng input limit làm ngữ cảnh đầu vào
                    max_input_tokens=input_limit,
                    max_output_tokens=output_limit
                )
                
                # 2. Tạo Pricing mặc định (Giá thực tế thường được cấu hình tĩnh hoặc fetch từ DB/file cấu hình riêng)
                pricing_dto = PricingInfo()

                # 3. Tạo Object ModelInfo hoàn chỉnh
                # Lưu ý: Trường 'capabilities' lúc này sẽ tạm để trống (set()) hoặc 
                # sẽ được fill bởi ModelCapabilityManager bên ngoài sau khi hàm này trả về danh sách thô.
                model_info = ModelInfo(
                    id=model_id,
                    display_name=m.get("displayName", model_id),
                    provider=self.name,
                    family=model_id.split("-")[0] if "-" in model_id else model_id,
                    version=m.get("version", "v1"),
                    description=m.get("description", ""),
                    limits=limits_dto,
                    pricing=pricing_dto,
                    capabilities=set(),  # KHÔNG gọi capability_manager ở đây nữa để bẻ gãy vòng lặp vô hạn
                    owned_by="google",
                    metadata={
                        "supported_generation_methods": m.get("supportedGenerationMethods", []),
                        "top_p": m.get("topP"),
                        "top_k": m.get("topK"),
                        "temperature": m.get("temperature"),
                    }
                )
                final_models.append(model_info)
                
            except Exception as e:
                logger.error("Error mapping raw Gemini model data to ModelInfo DTO", model_id=model_id, error=str(e))
                continue

        logger.info(
            "Successfully mapped Gemini models list to Gateway DTOs", 
            raw_count=len(gemini_models), 
            mapped_count=len(final_models)
        )
        
        return ModelList(data=final_models)

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
    
    async def model(self, **kwargs) -> ModelInfo:
        """
        Lấy thông tin cấu hình chi tiết của một model cụ thể từ Gemini API.
        Chỉ thực hiện fetch và parse dữ liệu thô, loại bỏ hoàn toàn capability_manager.
        """
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        raw_model_name = kwargs.get("model_name")

        if not raw_model_name:
            logger.error("Missing required parameter 'model_name' in model details request")
            raise ValueError("Parameter 'model_name' is required.")

        # Chuẩn hóa tên model: loại bỏ "models/" nếu có để dựng endpoint
        clean_model_name = raw_model_name.replace("models/", "")
        api_endpoint_template = f"v1beta/models/{clean_model_name}"

        logger.info(
            "Requesting specific model metadata from Gemini",
            provider=self.name,
            requested_model=raw_model_name,
            resolved_endpoint=api_endpoint_template
        )

        try:
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=api_endpoint_template, 
                timeout=timeout
            )
            
            gemini_raw_data = response.json()
            
            logger.info(
                "Successfully fetched model metadata",
                provider=self.name,
                model_id=gemini_raw_data.get("name"),
                display_name=gemini_raw_data.get("displayName")
            )
            
            # --- PARSE DỮ LIỆU SANG MODELINFO DTO ---
            raw_name = gemini_raw_data.get("name", "")
            model_id = raw_name.replace("models/", "") if raw_name else clean_model_name
            
            # 1. Trích xuất giới hạn tokens từ payload chi tiết của Gemini
            input_limit = gemini_raw_data.get("inputTokenLimit", 32768)
            output_limit = gemini_raw_data.get("outputTokenLimit", 8192)
            
            limits_dto = ContextLimits(
                context_window=input_limit,
                max_input_tokens=input_limit,
                max_output_tokens=output_limit
            )
            
            # 2. Khởi tạo Pricing trống (Cấu hình tĩnh ở tầng trên xử lý)
            pricing_dto = PricingInfo()

            # 3. Trả về ModelInfo với capabilities trống để bên ngoài tự map
            return ModelInfo(
                id=model_id,
                display_name=gemini_raw_data.get("displayName", model_id),
                provider=self.name,
                family=model_id.split("-")[0] if "-" in model_id else model_id,
                version=gemini_raw_data.get("version", "v1"),
                description=gemini_raw_data.get("description", ""),
                limits=limits_dto,
                pricing=pricing_dto,
                capabilities=set(),  # KHÔNG gọi capability_manager tại đây
                owned_by="google",
                metadata={
                    "supported_generation_methods": gemini_raw_data.get("supportedGenerationMethods", []),
                    "top_p": gemini_raw_data.get("topP"),
                    "top_k": gemini_raw_data.get("topK"),
                    "temperature": gemini_raw_data.get("temperature"),
                }
            )

        except Exception as e:
            logger.error(
                "Unexpected error occurred while fetching model details",
                provider=self.name,
                model_name=raw_model_name,
                error=str(e)
            )
            raise e
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
    async def upload_file(self, **kwargs) -> Any: raise NotImplementedError
    async def download_file(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_file(self, **kwargs) -> Any: raise NotImplementedError
    async def list_files(self, **kwargs) -> Any: raise NotImplementedError
    async def get_file(self, **kwargs) -> Any: raise NotImplementedError
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
        """Tải tệp lên Gemini File API và trả về thông tin tệp."""
        file_path = kwargs.get("file_path")
        display_name = kwargs.get("display_name")
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not file_path or not http_client:
            raise ValueError("`file_path` và `http_client` là bắt buộc.")

        # 1. Xác định MIME type
        mime_type = FileHelper.detect_mime_type(file_path)
        
        # 2. Chuẩn bị request body cho Gemini File API
        file_metadata = {
            "file": {
                "displayName": display_name or file_path.name,
                "mimeType": mime_type,
            }
        }

        # 3. Gửi request (Lưu ý: Gemini API v1beta yêu cầu upload 2 bước, đây là bước tạo metadata)
        # Bước thực sự upload file content sẽ cần một request khác tới upload_uri được trả về.
        # Tuy nhiên, nhiều thư viện client trừu tượng hóa điều này. Giả sử `send` có thể xử lý.
        response = await self.send(
            client=http_client,
            api_type="upload_file",
            json=file_metadata,
            timeout=timeout,
        )
        return await self.adapter.adapt_file_upload_response(response)

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