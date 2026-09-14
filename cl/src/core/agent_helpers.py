from enum import Enum
from typing import Any

def extract_text_content(content: Any) -> str:
    """Bóc tách văn bản thuần từ cấu trúc content dạng string, dict, object hoặc danh sách lồng ghép."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(extract_text_content(item) for item in content)

    if isinstance(content, dict):
        text_val = content.get("text")
        if text_val is not None:
            return extract_text_content(text_val)
        
        data_val = content.get("data")
        if isinstance(data_val, dict):
            return extract_text_content(data_val.get("data"))
        if data_val is not None:
            return str(data_val)
        return ""

    text_attr = getattr(content, "text", None)
    if text_attr is not None:
        return extract_text_content(text_attr)

    if hasattr(content, "data"):
        data_attr = getattr(content, "data")
        if isinstance(data_attr, dict):
            return extract_text_content(data_attr.get("data"))
        if data_attr is not None:
            return str(data_attr)

    return str(content)


def serialize_helper(obj: Any) -> Any:
    """Hỗ trợ serialize linh hoạt cho Enum, Pydantic model, Dict và List."""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (int, float, str, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {k: serialize_helper(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [serialize_helper(i) for i in obj]
    if hasattr(obj, "text") and getattr(obj, "text") is not None:
        return obj.text
    if hasattr(obj, "__dict__"):
        return {k: serialize_helper(v) for k, v in vars(obj).items()}
    return str(obj)