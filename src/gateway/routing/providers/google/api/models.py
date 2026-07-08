from typing import Dict, Any, List

from ...base import ApiType, BaseProvider
from .....schemas import (
    ModelInfo, ModelList,
    ContextLimits, PricingInfo
)
from ...base.interfaces.model import ModelProvider
import structlog 
logger = structlog.get_logger(__name__)

class GoogleModels(ModelProvider):
    def __init__(self, provider: BaseProvider):
        self.provider = provider
    
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
            provider=self.provider.name,
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
        
        logger.info("Fetching raw models list from Gemini API", provider=self.provider.name)
        
        try:
            response = await self.provider.send(
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
            logger.error("Failed to fetch or parse models from Gemini API", error=str(e), provider=self.provider.name)
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
            provider=self.provider.name,
            requested_model=raw_model_name,
            resolved_endpoint=f"v1beta/models/{clean_model_name}"
        )

        try:
            response = await self.provider.send(
                client=http_client,
                method="GET",
                api_type=ApiType.MODEL, 
                model=clean_model_name,
                timeout=timeout
            )
            
            gemini_raw_data = response.json()
            
            logger.info(
                "Successfully fetched model metadata",
                provider=self.provider.name,
                model_id=gemini_raw_data.get("name"),
                display_name=gemini_raw_data.get("displayName")
            )
            
            # Gọi hàm map chung, truyền thêm clean_model_name phòng trường hợp payload không trả về field 'name'
            return self._map_to_model_info(gemini_raw_data=gemini_raw_data, fallback_id=clean_model_name)

        except Exception as e:
            logger.error(
                "Unexpected error occurred while fetching model details",
                provider=self.provider.name,
                model_name=raw_model_name,
                error=str(e)
            )
            raise e
        
    async def model_capabilities(self, **kwargs) -> Any: raise NotImplementedError