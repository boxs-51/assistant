from typing import Dict

from .api import ApiType


class ApiTypeMapper:
    """
    Chịu trách nhiệm dịch một ApiType chung sang một chuỗi endpoint template
    cụ thể mà provider hỗ trợ.
    """
    def __init__(self, api_map: Dict[ApiType, str]):
        self.api_map = api_map

    def get_template(self, api_type: ApiType) -> str:
        """Lấy template cho một ApiType. Ném lỗi nếu không tìm thấy."""
        template = self.api_map.get(api_type)
        if not template:
            raise ValueError(f"API type '{api_type.name}' is not mapped for this provider.")
        return template