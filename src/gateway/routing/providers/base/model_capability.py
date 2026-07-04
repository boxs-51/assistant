from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Set, TYPE_CHECKING
import httpx
import structlog
import os
import json
import time

from .capability import ModelCapability

if TYPE_CHECKING:
    from .provider import BaseProvider

logger = structlog.get_logger(__name__)

class BaseModelCapabilityManager(ABC):
    """
    Lớp trừu tượng quản lý việc khám phá và cache năng lực của các model.
    """
    def __init__(self, provider_name: str, cache_dir: str = ".cache", cache_ttl_seconds: int = 86400):
        self._models_cache: List[Dict[str, Any]] | None = None
        self._capabilities_cache: Dict[str, Set[ModelCapability]] = {}
        self.provider_name = provider_name
        self.cache_ttl = cache_ttl_seconds
        
        # --- THÊM CỜ TRẠNG THÁI CHỐNG VÒNG LẶP / ĐỒNG THỜI ---
        self._is_refreshing = False 

        # Thiết lập đường dẫn file cache
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        self.cache_file_path = os.path.join(cache_dir, f"{provider_name}_models.json")

        # Tải cache từ đĩa khi khởi tạo
        self._load_from_disk_cache()
        
    async def get_capabilities_for_model(
        self,
        provider: BaseProvider,
        model_name: str,
        http_client: httpx.AsyncClient,
        timeout: float
    ) -> Set[ModelCapability]:
        """
        Lấy tập hợp năng lực cho một model cụ thể, tự động làm mới cache nếu cần.
        Chỉ cho phép gọi API làm mới 1 lần duy nhất, chống vòng lặp chéo.
        """
        # Nếu cache trống và chưa có tiến trình nào đang refresh
        if self._models_cache is None:
            if self._is_refreshing:
                # Nếu chính tiến trình này hoặc luồng khác đang refresh mà lại nhảy vào đây
                # Điều này chứng tỏ đang bị VÒNG LẶP CHÉO (Circular Call) từ provider.models()
                logger.warning(
                    "Circular capability resolution detected or concurrent refresh in progress. "
                    "Returning fallback empty capabilities to prevent infinite loop.",
                    provider=provider.name,
                    model_name=model_name
                )
                return set()

            logger.info("Model capabilities cache is empty, refreshing...", provider=provider.name)
            
            try:
                # Tiến hành refresh cache
                await self._refresh_cache(provider, http_client, timeout)
            except Exception as e:
                logger.error(
                    "Failed to refresh cache during capability resolution. Propagating error.", 
                    provider=provider.name, 
                    error=str(e)
                )
                # Ném lỗi ra ngoài ngay lập tức, không thử lại
                raise e

        # Trả về kết quả sau khi đã xử lý cache thành công (hoặc lỗi đã văng ra ngoài)
        provider_model_name = provider.mapper.translate(model_name)
        return self._capabilities_cache.get(provider_model_name, set())

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

            # Khôi phục capabilities cache từ dạng list sang set
            self._capabilities_cache = {
                model: {ModelCapability[cap_name] for cap_name in caps}
                for model, caps in disk_cache.get("capabilities", {}).items()
            }
            self._models_cache = disk_cache.get("models", [])
            logger.info("Successfully loaded model capabilities from disk cache.", provider=self.provider_name, model_count=len(self._capabilities_cache))

        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to load from disk cache.", path=self.cache_file_path, error=str(e))
            self._models_cache = None
            self._capabilities_cache = {}

    def _save_to_disk_cache(self):
        """Lưu cache hiện tại vào file JSON trên đĩa."""
        serializable_caps = {model: [cap.name for cap in caps] for model, caps in self._capabilities_cache.items()}
        disk_cache = {"timestamp": time.time(), "models": self._models_cache, "capabilities": serializable_caps}
        with open(self.cache_file_path, 'w', encoding='utf-8') as f:
            json.dump(disk_cache, f, indent=2)
        logger.info("Saved model capabilities to disk cache.", provider=self.provider_name, path=self.cache_file_path)

    async def _refresh_cache(self, provider: BaseProvider, http_client: httpx.AsyncClient, timeout: float):
        """
        Gọi API để lấy danh sách model và xây dựng cache năng lực.
        """
        # Bật cờ đánh dấu đang trong quá trình refresh
        self._is_refreshing = True
        try:
            # Gọi API lấy danh sách gốc (provider.models giờ sẽ chạy mà không bị lặp vô hạn)
            model_list_obj = await provider.models(http_client=http_client, timeout=timeout)
            
            model_info_list = model_list_obj.data
            self._models_cache = []

            new_capabilities_cache = {}
            for model_info in model_info_list:
                model_dump = model_info.model_dump()
                self._models_cache.append(model_dump)
                model_id = model_dump.get("id")
                if model_id: # Đảm bảo model_id tồn tại
                    caps = self._parse_model_capabilities(model_dump)
                    new_capabilities_cache[model_id] = caps
            
            self._capabilities_cache = new_capabilities_cache
            self._save_to_disk_cache()

            logger.info("Successfully refreshed model capabilities cache.", provider=provider.name, model_count=len(new_capabilities_cache))
        except Exception as e:
            logger.error("Failed to refresh model capabilities cache.", provider=provider.name, error=str(e))
            # Nếu lỗi, dọn dẹp cache về trạng thái None để lần yêu cầu thực sự tiếp theo có thể thử lại
            self._models_cache = None
            self._capabilities_cache = {}
            raise e
        finally:
            # BẮT BUỘC: Hạ cờ trạng thái dù thành công hay thất bại
            self._is_refreshing = False

    @abstractmethod
    def _parse_model_capabilities(self, model_info: Dict[str, Any]) -> Set[ModelCapability]:
        """
        Lớp con phải triển khai logic để phân tích năng lực từ thông tin của một model.
        """
        raise NotImplementedError

class DefaultModelCapabilityManager(BaseModelCapabilityManager):
    """Một trình quản lý mặc định với logic phân tích cơ bản."""
    def _parse_model_capabilities(self, model_info: Dict[str, Any]) -> Set[ModelCapability]:
        caps = set()
        model_id = model_info.get("id", "").lower()
        
        # Logic phân tích cơ bản dựa trên tên model
        if "chat" in model_id or "instruct" in model_id or "gemini" in model_id:
            caps.add(ModelCapability.CHAT)
            caps.add(ModelCapability.CHAT_STREAM)
        if "vision" in model_id or "llava" in model_id:
            caps.add(ModelCapability.VISION)
        if "embed" in model_id:
            caps.add(ModelCapability.EMBEDDINGS)
        if "image" in model_id:
            caps.add(ModelCapability.IMAGE_GENERATION)
        if "tts" in model_id or "speech" in model_id:
            caps.add(ModelCapability.TEXT_TO_SPEECH)
        
        return caps
    
class GeminiModelCapabilityManager(BaseModelCapabilityManager):
    """Trình quản lý năng lực tối ưu riêng cho Google Gemini API."""
    
    def _parse_model_capabilities(self, model_info: Dict[str, Any]) -> Set[ModelCapability]:
        caps = set()
        model_id = model_info.get("id", "").lower()
        
        # Lấy danh sách các method từ API của Google (nếu có truyền qua payload thô)
        # hoặc check dựa theo cấu trúc tên chuẩn của thế hệ Gemini 2.x
        supported_methods = model_info.get("supportedGenerationMethods", [])
        
        # 1. Kiểm tra năng lực Chat & Chat Stream cơ bản
        if "generateContent" in supported_methods or "gemini" in model_id:
            caps.add(ModelCapability.CHAT)
            caps.add(ModelCapability.CHAT_STREAM)
            
        # 2. Xử lý năng lực Multimodal (Vision / Audio) 
        # Toàn bộ thế hệ Gemini 2.0 và 2.5 (Flash, Pro, Lite) đều tích hợp sẵn Vision diện rộng
        if any(version in model_id for version in ["gemini-2.0", "gemini-2.5"]):
            caps.add(ModelCapability.VISION)
            # Nếu hệ thống của bạn có hỗ trợ các nhãn năng lực khác như AUDIO, bạn có thể add thêm tại đây
            
        # 3. Fallback cho các model thế hệ cũ hoặc dạng đặc thù khác nếu có xuất hiện trong list
        else:
            if "vision" in model_id:
                caps.add(ModelCapability.VISION)
            if "embed" in model_id:
                caps.add(ModelCapability.EMBEDDINGS)
                
        return caps