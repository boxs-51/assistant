import structlog
import fnmatch
import yaml
import os
import asyncio

from typing import Dict, List

from ...infrastructure.config import settings
from ..core.provider import BaseProvider

logger = structlog.get_logger(__name__)

class RoutingPolicy:
    """
    REFACTORED: Chứa logic phân giải model thành chuỗi provider.
    Hỗ trợ tải quy tắc từ file YAML và reload nóng (hot-reload).
    """
    def __init__(self, providers: Dict[str, BaseProvider]):
        self.providers = providers
        self._default_chain: List[BaseProvider] = []
        self._rules: List[Dict] = []
        self._reload_lock = asyncio.Lock() # Lock để đảm bảo an toàn khi reload
        self._initialize()

    def _initialize(self):
        """
        Khởi tạo các quy tắc định tuyến và chuỗi fallback mặc định từ settings.
        """
        # Xây dựng chuỗi fallback mặc định từ config
        self._default_chain = [self.providers[name] for name in settings.provider.priority if name in self.providers]
        #if "mock" in self.providers and "mock" not in [p.name for p in self._default_chain]:
            # Offline Phase 0 mode: mock is appended only when explicitly enabled.
        #    self._default_chain.append(self.providers["mock"])
        logger.info(
            "Default chain",
            chain=[p.name for p in self._default_chain]
        )
        # Tải quy tắc từ file YAML
        self._load_rules_from_file()

    def _load_rules_from_file(self):
        """Tải và xử lý các quy tắc định tuyến từ file YAML."""
        new_rules = []
        if not os.path.exists(settings.provider.routing_rules_path):
            logger.error("Routing rules file not found.", path=settings.provider.routing_rules_path)
            self._rules = []
            
            return

        try:
            with open(settings.provider.routing_rules_path, 'r', encoding='utf-8') as f:
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
            logger.info("Routing rules loaded successfully.", rule_count=len(self._rules), path=settings.provider.routing_rules_path)

        except (yaml.YAMLError, FileNotFoundError, Exception) as e:
            logger.error("Failed to load or parse routing rules file. No rules will be applied.", error=str(e), path=settings.provider.routing_rules_path)
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

    def get_fallback_chain(self, model: str) -> List[BaseProvider]:
        """Lấy chuỗi fallback các provider phù hợp cho một model cụ thể."""
        for rule in self._rules:
            for model_pattern in rule["models"]:
                if fnmatch.fnmatch(model, model_pattern):
                    logger.debug("Routing rule matched", model=model, pattern=model_pattern, chain=[p.name for p in rule["chain"]])
                    return rule["chain"]
        
        logger.debug("No specific routing rule matched, using default chain", model=model)
        return self._default_chain