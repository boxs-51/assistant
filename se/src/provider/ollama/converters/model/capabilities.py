from typing import Set, Dict, Any
from .....domain.schemas import ModelCapability

VISION_KEYWORDS = {"llava", "vision", "moondream", "mllama", "bakllava", "qwen2-vl", "minicpm-v"}
TOOL_KEYWORDS = {"qwen2.5","qwen3", "llama3.1", "llama3.2", "mistral", "mixtral", "command-r"}
EMBED_KEYWORDS = {"embed", "nomic-embed", "bge-"}


class OllamaCapabilityResolver:
    """Xử lý phát hiện đầy đủ năng lực của Ollama model qua Heuristics và /api/show."""

    @staticmethod
    def detect_from_name(model_id: str) -> Set[ModelCapability]:
        m = model_id.lower()
        if any(k in m for k in EMBED_KEYWORDS):
            return {ModelCapability.EMBEDDING}

        caps = {ModelCapability.CHAT, ModelCapability.CHAT_STREAM}
        if any(k in m for k in VISION_KEYWORDS):
            caps.add(ModelCapability.VISION)
        if any(k in m for k in TOOL_KEYWORDS):
            caps.add(ModelCapability.FUNCTION_CALLING)
        return caps

    @staticmethod
    def detect_from_show_response(show_data: Dict[str, Any]) -> Set[ModelCapability]:
        caps = {ModelCapability.CHAT, ModelCapability.CHAT_STREAM}
        template = show_data.get("template", "").lower()
        modelfile = show_data.get("modelfile", "").lower()
        details = show_data.get("details", {})
        families = [f.lower() for f in details.get("families", [])]

        # 1. Function Calling detection
        if "tools" in template or ".tools" in template:
            caps.add(ModelCapability.FUNCTION_CALLING)

        # 2. Vision/Multimodal detection
        if any(f in {"mllama", "llava", "clip"} for f in families) or "clip" in modelfile:
            caps.add(ModelCapability.VISION)

        return caps