import httpx
import structlog
import asyncio
from typing import Dict, Any, AsyncGenerator

# 1. Import các thành phần đã được module hóa
from ..base.provider import BaseProvider
from ..base.api import ApiType
from ..base.auth import ApiKeyInQuery
from ..base.api_mapper import ApiTypeMapper
from ..base.endpoint import EndpointBuilder
from ..base.model_capability import DefaultModelCapabilityManager
from ..base.capability import ProviderCapability
from ..base.model_mapper import ModelMapper
from .adapter import GeminiAdapter # Adapter chuyên biệt

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk, ModelList, ModelInfo

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
            capability_manager=DefaultModelCapabilityManager(provider_name="gemini"), # Có thể tạo GeminiCapabilityManager riêng sau này
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
        provider_model = prepared_body.get("model", "default") # Lấy model đã được dịch
        
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
        provider_model = prepared_body.get("model", "default")

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

    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model từ Gemini và phân tích capability động."""
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
            
            # httpx response.json() là đồng bộ, nhưng nếu dùng thư viện custom có thể cần await. 
            # Giữ nguyên theo code gốc của bạn:
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
        models_list = gemini_data.get("models", [])
        logger.info("Found models in Gemini response, starting capability resolution", count=len(models_list))

        async def get_model_info(model_data: Dict[str, Any]) -> ModelInfo | None:
            raw_name = model_data.get("name", "")
            # Gemini dùng 'name' có prefix 'models/', ví dụ 'models/gemini-2.5-flash'
            model_id = raw_name.replace("models/", "")
            
            if not model_id:
                logger.warning("Skipping model entry with empty or invalid name attribute", model_data=model_data)
                return None
            
            logger.debug("Resolving capabilities for specific model", model_id=model_id, raw_name=raw_name)
            
            try:
                # Truyền raw_name hoặc model_id tùy thuộc vào capability_manager của bạn yêu cầu chuỗi nào
                capabilities_set = await self.capability_manager.get_capabilities_for_model(
                    provider=self, 
                    model_name=raw_name, # Khuyên dùng raw_name đầy đủ nếu hàm map dựa trên "models/..."
                    http_client=http_client, 
                    timeout=timeout
                )
                
                cap_names = [cap.name for cap in capabilities_set]
                logger.debug(
                    "Successfully resolved capabilities for model", 
                    model_id=model_id, 
                    capabilities=cap_names
                )
                
                return ModelInfo(
                    id=model_id, 
                    owned_by="google", 
                    capabilities=cap_names
                )
            except Exception as e:
                # Log lỗi của từng model riêng lẻ để không làm sập toàn bộ tiến trình lấy list
                logger.error(
                    "Error resolving capabilities for model, skipping this model", 
                    model_id=model_id, 
                    error=str(e)
                )
                return None

        # Tạo danh sách các task chạy song song
        tasks = [get_model_info(m) for m in models_list]
        
        logger.debug("Executing concurrent capability resolution tasks", task_count=len(tasks))
        results = await asyncio.gather(*tasks)
        
        # Lọc bỏ các kết quả None do lỗi hoặc dữ liệu trống
        final_models = [info for info in results if info]
        
        logger.info(
            "Successfully completed processing Gemini models list", 
            requested_count=len(models_list), 
            successful_count=len(final_models)
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
        Tận dụng tính năng truyền trực tiếp chuỗi URL template vào hàm send().
        """
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        raw_model_name = kwargs.get("model_name")

        if not raw_model_name:
            logger.error("Missing required parameter 'model_name' in model details request")
            raise ValueError("Parameter 'model_name' is required.")

        # Chuẩn hóa tên model: loại bỏ "models/" nếu có
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
            
            # --- CẬP NHẬT QUAN TRỌNG: Map dữ liệu sang đúng cấu trúc ModelInfo ---
            raw_name = gemini_raw_data.get("name", "")
            model_id = raw_name.replace("models/", "") if raw_name else clean_model_name
            
            # Lấy danh sách capabilities tương tự như hàm `models` tổng thể
            capabilities_set = await self.capability_manager.get_capabilities_for_model(
                provider=self, 
                model_name=raw_name or f"models/{clean_model_name}",
                http_client=http_client, 
                timeout=timeout
            )
            cap_names = [cap.name for cap in capabilities_set]

            # Khởi tạo chuẩn xác theo Schema Pydantic của bạn
            return ModelInfo(
                id=model_id,
                owned_by="google",
                capabilities=cap_names
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