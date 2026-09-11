from typing import Dict, Any, Set, Optional
from .....domain.schemas import ModelInfo, ContextLimits


class OllamaModelAdapter:
    """Adapter chuyển đổi payload chi tiết từ /api/show của Ollama sang DTO ModelInfo."""

    @staticmethod
    def to_model_info(
        model_id: str,
        provider_name: str,
        capabilities: Set[Any],
        show_details: Optional[Dict[str, Any]] = None,
    ) -> ModelInfo:
        family = None
        description = ""
        context_window = 32768

        if show_details:
            details = show_details.get("details", {})
            
            # Trích xuất family chuẩn từ details của Ollama
            family = details.get("family")
            
            # Trích xuất parameter size & quantization làm description
            param_size = details.get("parameter_size", "")
            quant = details.get("quantization_level", "")
            if param_size or quant:
                description = f"Params: {param_size}, Quant: {quant}".strip(", ")

            # Trích xuất context length từ model_info
            model_info = show_details.get("model_info", {})
            for key, value in model_info.items():
                if key.endswith(".context_length") or key.endswith(".max_position_embeddings"):
                    try:
                        context_window = int(value)
                        break
                    except (ValueError, TypeError):
                        pass

        # Fallback family nếu trong details không có
        if not family:
            family = model_id.split(":")[0] if ":" in model_id else model_id.split("-")[0]

        return ModelInfo(
            id=model_id,
            display_name=model_id,
            provider=provider_name,
            family=family,
            version="v1",
            description=description,
            limits=ContextLimits(context_window=context_window, max_output_tokens=4096),
            capabilities=capabilities,
            owned_by="ollama",
        )