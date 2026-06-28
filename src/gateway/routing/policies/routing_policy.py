from typing import Dict, List
import structlog
import fnmatch

from ...config import settings
from ..providers.base import BaseProvider

logger = structlog.get_logger(__name__)

class RoutingPolicy:
    """
    Strategy Pattern: Chứa logic để phân giải một tên model thành một chuỗi fallback các provider.
    Hỗ trợ các quy tắc được định nghĩa một cách tường minh và so khớp wildcard.
    """
    def __init__(self, providers: Dict[str, BaseProvider]):
        self.providers = providers
        self._default_chain: List[BaseProvider] = []
        self._rules: List[Dict] = []
        self._initialize()

    def _initialize(self):
        """
        Khởi tạo các quy tắc định tuyến và chuỗi fallback mặc định từ settings.
        """
        # 1. Xây dựng chuỗi fallback mặc định từ config
        self._default_chain = [self.providers[name] for name in settings.PROVIDER_PRIORITY if name in self.providers]

        # 2. Định nghĩa các quy tắc định tuyến một cách tường minh.
        # Cấu trúc này có thể được tải từ file YAML/JSON để có thể reload nóng.
        # Thứ tự của các quy tắc là quan trọng: quy tắc đầu tiên khớp sẽ được sử dụng.
        routing_rules_config = [
            {
                "models": ["gpt-4o", "gpt-3.5-turbo", "gpt-4*"],
                "provider_chain": ["openai", "gemini", "anthropic"]
            },
            {
                "models": ["gemini-pro", "gemini-1.5-pro", "gemini*"],
                "provider_chain": ["gemini", "openai", "anthropic"]
            },
            # Quy tắc này giải quyết yêu cầu của bạn: các model local sẽ được định tuyến đến ollama trước.
            {
                "models": ["llama*", "codellama*", "mistral*", "phi3*"],
                "provider_chain": ["ollama", "openai"] # Ví dụ: thử local trước, nếu thất bại thì fallback ra cloud
            }
        ]

        # 3. Xử lý các quy tắc để tạo ra một bộ quy tắc có thể sử dụng được
        for rule_config in routing_rules_config:
            # [REFACTOR] Log a warning for any provider in a rule that isn't available.
            # This makes debugging configuration errors much easier.
            for p_name in rule_config["provider_chain"]:
                if p_name not in self.providers:
                    logger.warning(
                        "Provider in routing rule is not available/configured.",
                        rule_models=rule_config["models"], missing_provider=p_name
                    )
            provider_chain = [self.providers[p_name] for p_name in rule_config["provider_chain"] if p_name in self.providers]
            if provider_chain:
                self._rules.append({"models": rule_config["models"], "chain": provider_chain})
        
        logger.info("RoutingPolicy initialized", default_fallback_chain=[p.name for p in self._default_chain], rule_count=len(self._rules))

    def get_fallback_chain(self, model: str) -> List[BaseProvider]:
        """Lấy chuỗi fallback các provider phù hợp cho một model cụ thể."""
        for rule in self._rules:
            for model_pattern in rule["models"]:
                if fnmatch.fnmatch(model, model_pattern):
                    logger.debug("Routing rule matched", model=model, pattern=model_pattern, chain=[p.name for p in rule["chain"]])
                    return rule["chain"]
        
        logger.debug("No specific routing rule matched, using default chain", model=model)
        return self._default_chain