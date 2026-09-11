import structlog
import fnmatch
import yaml
import os
import asyncio

from typing import Dict, List, Any

from ...infrastructure.config.schemas import ProviderSettings

from ..core.provider import BaseProvider

logger = structlog.get_logger(__name__)

class RoutingPolicy:
    """
    REFACTORED: Chứa logic phân giải model thành chuỗi provider.
    Hỗ trợ tải quy tắc từ file YAML và reload nóng (hot-reload).
    """
    def __init__(
        self,
        providers: Dict[str, BaseProvider],
        *,
        priority: List[str] | None = None,
        rules_path: str | None = None,
        config: ProviderSettings = None,
    ):
        self.providers = providers
        self._default_chain: List[BaseProvider] = []
        self._rules: List[Dict] = []
        self._reload_lock = asyncio.Lock()
        self._config = config
        self._initialize()

    def _initialize(self):
        """
        Khởi tạo các quy tắc định tuyến và chuỗi fallback mặc định từ config.
        """
        # Xây dựng chuỗi fallback mặc định từ config
        self._default_chain = [
            self.providers[name]
            for name in self._config.priority
            if name in self.providers
        ]
        if self._config.configs.get("mock").enabled:
            self._default_chain = [self.providers["mock"]]
        logger.info(
            "Default chain",
            chain=[p.name for p in self._default_chain]
        )
        # Tải quy tắc từ file YAML
        self._load_rules_from_file()

    def _load_rules_from_file(self):
        """Tải và xử lý các quy tắc định tuyến từ file YAML."""
        new_rules = []
        if not os.path.exists(self._config.routing_rules_path):
            logger.error("Routing rules file not found.", path=self._config.routing_rules_path)
            self._rules = []
            
            return

        try:
            with open(self._config.routing_rules_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            routing_rules_config = config.get("rules", [])

            for rule_config in routing_rules_config:
                # Log cảnh báo nếu một provider trong quy tắc không được cấu hình/khả dụng.
                for p_name in rule_config.get("provider_chain", []):
                    if p_name not in self.providers:
                        logger.warning(
                            "Provider in routing rule is not available/configured.",
                            rule_name=rule_config.get("name"), missing_provider=p_name
                        )
                
                provider_chain = [self.providers[p_name] for p_name in rule_config.get("provider_chain", []) if p_name in self.providers]
                
                if provider_chain:
                    new_rules.append({"models": rule_config["models"], "chain": provider_chain})
            
            self._rules = new_rules
            logger.info("Routing rules loaded successfully.", rule_count=len(self._rules), path=self._config.routing_rules_path)

        except (yaml.YAMLError, FileNotFoundError, Exception) as e:
            logger.error("Failed to load or parse routing rules file. No rules will be applied.", error=str(e), path=self._config.routing_rules_path)
            self._rules = [] # Xóa các quy tắc cũ nếu file mới bị lỗi để tránh hành vi không mong muốn

    async def reload_rules(self) -> bool:
        """Tải lại các quy tắc định tuyến từ file một cách an toàn."""
        async with self._reload_lock:
            logger.info("Attempting to hot-reload routing rules...")
            try:
                self._load_rules_from_file()
                return True
            except Exception:
                logger.error("Hot-reload of routing rules failed.", exc_info=True)
                return False

    def get_fallback_chain(
        self,
        model: str,
        metadata: Dict[str, Any] | None = None,
        override_provider: str | None = None,
    ) -> List[BaseProvider]:
        """
        Lấy chuỗi fallback provider phù hợp cho model dựa trên quy tắc cấu hình và metadata.
        
        Metadata spec:
        metadata = {
            "routing": {
                "prefer_provider": "openai", # Tên provider muốn ưu tiên
                "type": "fallback"          # "fallback" (mặc định) hoặc "strict" / "direct"
            }
        }
        """
        # 1. Tìm chuỗi provider cơ sở theo quy tắc pattern matching hoặc default chain
        base_chain = self._get_base_chain_for_model(model)

        # 2. Bóc tách dữ liệu routing từ metadata
        metadata = metadata or {}
        routing_info = metadata.get("routing", {})
        
        # Ưu tiên lấy từ override_provider (top-level body "provider"), nếu không có mới lấy prefer_provider trong metadata
        preferred_name = override_provider or routing_info.get("prefer_provider")
        routing_type = str(routing_info.get("type", "fallback")).lower()

        # Nếu không chỉ định provider ưu tiên, trả về chuỗi mặc định
        if not preferred_name:
            return base_chain

        # Kiểm tra xem provider được yêu cầu có tồn tại/được đăng ký không
        if preferred_name not in self.providers:
            logger.warning(
                "Preferred provider is not available or registered.",
                preferred_provider=preferred_name,
                routing_type=routing_type,
            )
            # Chế độ strict mà provider không khả dụng -> Trả về danh sách rỗng để chặn request ngay
            if routing_type in ("strict", "direct"):
                return []
            return base_chain

        target_provider = self.providers[preferred_name]

        # 3. Xử lý tạo chuỗi thực thi dựa trên routing `type`
        if routing_type in ("strict", "direct"):
            # Chế độ Strict: Chỉ gọi duy nhất provider này, không fallback
            return [target_provider]

        # Chế độ Fallback: Đưa target_provider lên đầu, giữ các provider còn lại phía sau
        others = [p for p in base_chain if p.name != preferred_name]
        return [target_provider] + others

    def _get_base_chain_for_model(self, model: str) -> List[BaseProvider]:
        """Hàm phụ trợ tìm chuỗi mặc định dựa trên file rules YAML."""
        for rule in self._rules:
            for model_pattern in rule["models"]:
                if fnmatch.fnmatch(model, model_pattern):
                    logger.debug("Routing rule matched", model=model, pattern=model_pattern)
                    return rule["chain"]
        
        logger.debug("No specific routing rule matched, using default chain", model=model)
        return self._default_chain