import os
import json
import time
import httpx
import structlog
from typing import Dict, Any, List, Set, TYPE_CHECKING, Optional, Union

from ....schemas import ModelInfo, ModelList ,ModelCapability, ProviderCapability

if TYPE_CHECKING:
    from ..provider import BaseProvider

logger = structlog.get_logger(__name__)

class ModelCapabilityManager:
    """
    Trình quản lý tập trung việc khám phá, phân tích và cache năng lực của các model.
    Hỗ trợ định tuyến logic phân tích theo từng nhà cung cấp (Provider) cụ thể.
    """
    def __init__(self, provider_name: str, cache_dir: str = ".cache", cache_ttl_seconds: int = 86400):
        self._models_cache: Optional[List[Dict[str, Any]]] = None
        self._capabilities_cache: Dict[str, Set[ModelCapability]] = {}
        self.provider_name = provider_name.lower()
        self.cache_ttl = cache_ttl_seconds
        
        # Cờ trạng thái chống vòng lặp chéo / chạy đồng thời
        self._is_refreshing = False 

        # Thiết lập đường dẫn file cache
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        self.cache_file_path = os.path.join(cache_dir, f"{self.provider_name}_models.json")

        # Tải cache từ đĩa khi khởi tạo
        self._load_from_disk_cache()
        
    async def get_capabilities_for_model(
        self,
        provider: "BaseProvider",
        model_name: str,
        http_client: httpx.AsyncClient,
        timeout: float
    ) -> Set[ModelCapability]:
        """Lấy tập hợp năng lực cho một model cụ thể, tự động làm mới cache nếu cần."""
        provider_model_name = provider.mapper.translate(model_name)
        
        # Nếu model chưa có trong cache và không trong quá trình refresh, tiến hành cập nhật
        if provider_model_name not in self._capabilities_cache:
            if self._is_refreshing:
                logger.warning(
                    "Circular capability resolution detected or concurrent refresh in progress. "
                    "Returning fallback empty capabilities to prevent infinite loop.",
                    provider=provider.name,
                    model_name=model_name
                )
                return set()

            logger.info("Model missing from cache, refreshing for specific model...", provider=provider.name, model=provider_model_name)
            
            try:
                # THAY ĐỔI: Truyền chính xác target_model_name vào để chỉ fetch đúng model đó
                await self._refresh_cache(provider, http_client, timeout, target_model_name=provider_model_name)
            except Exception as e:
                logger.error(
                    "Failed to refresh cache during capability resolution. Propagating error.", 
                    provider=provider.name, 
                    error=str(e)
                )
                raise e

        return self._capabilities_cache.get(provider_model_name, set())

    # =================================================================
    # HÀM PUBLIC: BUILD / ENRICH CAPABILITIES CHO DTO TỪ BÊN NGOÀI
    # =================================================================
    
    def enrich_capabilities(self, data: Union[ModelInfo, ModelList]) -> Union[ModelInfo, ModelList]:
        """
        Nhận vào một ModelInfo hoặc ModelList (chưa có capabilities), tự động phân tích, 
        gán tập hợp capabilities vào DTO, đồng bộ vào RAM cache và lưu xuống ổ đĩa.
        """
        if self._models_cache is None:
            self._models_cache = []

        is_updated = False

        if isinstance(data, ModelInfo):
            # 1. Phân tích năng lực
            data.capabilities = self._parse_model_capabilities(data)
            
            # 2. Cập nhật vào Runtime Memory Cache
            self._capabilities_cache[data.id] = data.capabilities
            
            # 3. Cập nhật thông tin thô vào danh sách cache (tránh trùng lặp)
            model_dump = data.model_dump()
            self._models_cache = [m for m in self._models_cache if m.get("id") != data.id]
            self._models_cache.append(model_dump)
            is_updated = True
            
        elif isinstance(data, ModelList):
            for model_info in data.data:
                # 1. Phân tích năng lực
                model_info.capabilities = self._parse_model_capabilities(model_info)
                
                # 2. Cập nhật vào Runtime Memory Cache
                self._capabilities_cache[model_info.id] = model_info.capabilities
                
                # 3. Cập nhật thông tin thô vào danh sách cache
                model_dump = model_info.model_dump()
                self._models_cache = [m for m in self._models_cache if m.get("id") != model_info.id]
                self._models_cache.append(model_dump)
            is_updated = True

        # 4. GHI CACHE XUỐNG Ổ ĐĨA (Nếu có dữ liệu thay đổi)
        if is_updated:
            self._save_to_disk_cache()
            
        return data

    # =================================================================
    # CẬP NHẬT: LUỒNG REFRESH CACHE TỐI ƯU (GỌI .model() HOẶC .models())
    # =================================================================

    async def _refresh_cache(
        self, 
        provider: "BaseProvider", 
        http_client: httpx.AsyncClient, 
        timeout: float, 
        target_model_name: Optional[str] = None
    ):
        """
        Gọi API để cập nhật cache. Hỗ trợ gọi chi tiết một model lẻ hoặc toàn bộ danh sách.
        Đồng bộ trực tiếp sử dụng cấu trúc mã nguồn ModelInfo mới.
        """
        self._is_refreshing = True
        try:
            if self._models_cache is None:
                self._models_cache = []

            # 1. KIỂM TRA ĐIỀU HƯỚNG ROUTE: Gọi đơn lẻ (.model) hoặc danh sách (.models)
            if target_model_name:
                logger.debug(
                    "Fetching single model details to optimize capability cache", 
                    provider=provider.name, 
                    model=target_model_name
                )
                model_info_obj = await provider.models.model(
                    model_name=target_model_name, 
                    http_client=http_client, 
                    timeout=timeout
                )
                model_info_list = [model_info_obj]
            else:
                logger.debug("Fetching complete models list for capability cache", provider=provider.name)
                model_list_obj = await provider.models.models(http_client=http_client, timeout=timeout)
                model_info_list = model_list_obj.data
                self._models_cache = []  # Reset cache danh sách cũ nếu làm mới toàn bộ

            # 2. PHÂN TÍCH VÀ ĐỒNG BỘ CAPABILITIES
            for model_info in model_info_list:
                # Phân tích năng lực bằng hàm xử lý tập trung (nhận vào dạng ModelInfo)
                caps = self._parse_model_capabilities(model_info)
                
                # Cập nhật trực tiếp tập hợp này vào object ModelInfo hiện tại
                model_info.capabilities = caps
                
                # Cập nhật vào Runtime Memory Cache (lưu trữ dạng Enum Set phục vụ Gateway Engine)
                self._capabilities_cache[model_info.id] = caps
                
                # Chuyển đổi ModelInfo thành Dict để ghi vào Disk Cache dạng JSON thô
                model_dump = model_info.model_dump()
                
                # Tránh trùng lặp bản ghi cũ trong danh sách cache thô khi cập nhật model đơn lẻ
                self._models_cache = [m for m in self._models_cache if m.get("id") != model_info.id]
                self._models_cache.append(model_dump)
            
            # 3. GHI CACHE XUỐNG Ổ ĐĨA
            self._save_to_disk_cache()

        except Exception as e:
            logger.error(
                "Failed to refresh model capabilities cache.", 
                provider=provider.name, 
                error=str(e)
            )
            raise e
        finally:
            self._is_refreshing = False

    # =================================================================
    # QUẢN LÝ DISK CACHE
    # =================================================================

    def _load_from_disk_cache(self):
        """Tải cache từ file JSON trên đĩa nếu tồn tại và chưa hết hạn."""
        if not os.path.exists(self.cache_file_path):
            logger.info("Disk cache file not found.", path=self.cache_file_path)
            return

        try:
            with open(self.cache_file_path, 'r', encoding='utf-8') as f:
                disk_cache = json.load(f)
            
            last_updated = disk_cache.get("timestamp", 0)
            if time.time() - last_updated > self.cache_ttl:
                logger.info("Disk cache is stale, will refresh on next call.", path=self.cache_file_path)
                return

            self._capabilities_cache = {
                model: {ModelCapability[cap_name] for cap_name in caps if cap_name in ModelCapability.__members__}
                for model, caps in disk_cache.get("capabilities", {}).items()
            }
            self._models_cache = disk_cache.get("models", [])
            logger.info("Successfully loaded model capabilities from disk cache.", provider=self.provider_name, model_count=len(self._capabilities_cache))

        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to load from disk cache.", path=self.cache_file_path, error=str(e))
            self._models_cache = None
            self._capabilities_cache = {}

    def _save_to_disk_cache(self):
        """Lưu cache hiện tại vào file JSON trên đĩa, xử lý ép kiểu set sang list."""
        serializable_caps = {
            model: [cap.name for cap in caps] 
            for model, caps in self._capabilities_cache.items()
        }
        
        # CHUẨN HÓA MODELS CACHE: Chuyển toàn bộ set thành list trong danh sách model thô
        serializable_models = []
        for m in (self._models_cache or []):
            # Sao chép nông để tránh thay đổi trực tiếp dữ liệu trong RAM
            model_copy = m.copy()
            if "capabilities" in model_copy and isinstance(model_copy["capabilities"], set):
                # Ép kiểu set sang list các chuỗi tên Enum hoặc chuỗi thô
                model_copy["capabilities"] = [
                    cap.name if hasattr(cap, "name") else str(cap) 
                    for cap in model_copy["capabilities"]
                ]
            serializable_models.append(model_copy)

        disk_cache = {
            "timestamp": time.time(), 
            "models": serializable_models, 
            "capabilities": serializable_caps
        }
        
        with open(self.cache_file_path, 'w', encoding='utf-8') as f:
            json.dump(disk_cache, f, indent=2)
            
        logger.info("Saved model capabilities to disk cache.", provider=self.provider_name, path=self.cache_file_path)

    # =================================================================
    # LOGIC PARSE THEO TỪNG PROVIDER
    # =================================================================

    def _parse_model_capabilities(self, model_info: ModelInfo) -> Set[ModelCapability]:
        """Phân tích và xác định tập hợp năng lực (capabilities) chi tiết cho ModelInfo."""
        caps: Set[ModelCapability] = set()
        
        provider = model_info.provider.lower()
        model_id = model_info.id.lower()
        metadata = model_info.metadata or {}

        # =================================================================
        # 1. PHÂN TÍCH CHO GOOGLE GEMINI (Dựa vào supportedGenerationMethods)
        # =================================================================
        if provider == "google" or "gemini" in provider:
            supported_methods = metadata.get("supported_generation_methods", [])
            
            # Chat & Stream
            if "generateContent" in supported_methods or "gemini" in model_id:
                caps.update({ModelCapability.CHAT, ModelCapability.CHAT_STREAM})
                # Hầu hết các dòng Gemini hiện đại đều mặc định hỗ trợ đếm Token và Tool Calling
                caps.update({ModelCapability.TOKEN_COUNT, ModelCapability.TOOL_CALLING})
                # Gemini hỗ trợ cấu hình responseMimeType sang application/json
                caps.update({ModelCapability.JSON_MODE, ModelCapability.STRUCTURED_OUTPUT})

            # Embeddings
            if "embedContent" in supported_methods or "embed" in model_id:
                caps.add(ModelCapability.EMBEDDINGS)
                if "batch" in model_id or "batch" in supported_methods:
                    caps.add(ModelCapability.EMBEDDINGS_BATCH)

            # Code Execution (Tính năng đặc trưng chạy Python trong sandbox của Gemini)
            if "gemini" in model_id and not any(x in model_id for x in ["embedding", "bidi"]):
                caps.add(ModelCapability.CODE_EXECUTION)

            # Vision & Video Understanding
            if any(version in model_id for version in ["gemini-2.0", "gemini-2.5", "gemini-1.5"]):
                caps.update({ModelCapability.VISION, ModelCapability.VIDEO_UNDERSTANDING})
            elif "vision" in model_id:
                caps.add(ModelCapability.VISION)

            # Search Grounding (Google Search)
            if any(x in model_id for x in ["online", "search"]) or "gemini-2" in model_id:
                caps.add(ModelCapability.WEB_SEARCH)

            # Audio
            if "audio" in model_id or "gemini-2.5" in model_id:
                caps.update({ModelCapability.SPEECH_TO_TEXT, ModelCapability.TEXT_TO_SPEECH})

        # =================================================================
        # 2. PHÂN TÍCH CHO OPENAI
        # =================================================================
        elif provider == "openai":
            # Chat Capabilities
            if any(x in model_id for x in ["gpt", "o1", "o3"]):
                caps.update({ModelCapability.CHAT, ModelCapability.CHAT_STREAM, ModelCapability.TOKEN_COUNT})
                caps.update({ModelCapability.TOOL_CALLING, ModelCapability.JSON_MODE, ModelCapability.STRUCTURED_OUTPUT})
                if "batch" in model_id:
                    caps.add(ModelCapability.CHAT_BATCH)
                
            # Vision
            if any(x in model_id for x in ["-vision", "gpt-4o", "o1", "o3"]):
                caps.add(ModelCapability.VISION)
                
            # Embeddings
            if "text-embedding" in model_id:
                caps.update({ModelCapability.EMBEDDINGS, ModelCapability.EMBEDDINGS_BATCH})
                
            # DALL-E (Images)
            if "dall-e" in model_id:
                caps.update({ModelCapability.IMAGE_GENERATION, ModelCapability.IMAGE_EDIT, ModelCapability.IMAGE_VARIATION})
                
            # Audio (Whisper & TTS)
            if "whisper" in model_id:
                caps.update({ModelCapability.SPEECH_TO_TEXT, ModelCapability.AUDIO_TRANSLATION})
            elif "tts" in model_id:
                caps.update({ModelCapability.TEXT_TO_SPEECH, ModelCapability.TEXT_TO_SPEECH_STREAM})

            # Moderation
            if "moderation" in model_id:
                caps.add(ModelCapability.MODERATION)

        # =================================================================
        # 3. PHÂN TÍCH CHO ANTHROPIC (CLAUDE)
        # =================================================================
        elif provider == "anthropic":
            if "claude" in model_id:
                caps.update({ModelCapability.CHAT, ModelCapability.CHAT_STREAM, ModelCapability.TOKEN_COUNT})
                caps.update({ModelCapability.TOOL_CALLING, ModelCapability.STRUCTURED_OUTPUT})
                
                # Claude 3 và 3.5 mặc định xử lý ảnh (Vision) cực tốt
                if any(v in model_id for v in ["claude-3", "claude-3-5"]):
                    caps.add(ModelCapability.VISION)

        # =================================================================
        # 4. CHUNG (CÁC PROVIDERS KHÁC NHƯ COHERE, MISTRAL, OLLAMA, RERANK)
        # =================================================================
        else:
            # Rerank models (Cohere/Jina)
            if "rerank" in model_id or "bge-reranker" in model_id:
                caps.add(ModelCapability.RERANK)
                return caps

            if any(x in model_id for x in ["chat", "instruct", "llama", "mistral", "qwen", "phi"]):
                caps.update({ModelCapability.CHAT, ModelCapability.CHAT_STREAM, ModelCapability.TOOL_CALLING})
                
            if any(x in model_id for x in ["vision", "llava", "vlm"]):
                caps.add(ModelCapability.VISION)
                
            if "embed" in model_id:
                caps.add(ModelCapability.EMBEDDINGS)

        return caps