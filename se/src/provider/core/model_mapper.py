from typing import Dict

class ModelMapper:
    """
    Chịu trách nhiệm dịch tên model từ tên mà người dùng yêu cầu
    sang tên model thực tế mà provider hỗ trợ.
    """
    def __init__(self, model_map: Dict[str, str]):
        self.model_map = model_map

    def translate(self, requested_model: str) -> str:
        """
        Dịch tên model. Nếu không tìm thấy mapping, trả về chính tên đã yêu cầu.
        """
        return self.model_map.get(requested_model, requested_model)