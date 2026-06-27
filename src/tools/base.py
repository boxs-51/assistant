from abc import ABC, abstractmethod

class BaseTool(ABC):
    def __init__(self, name: str, description: str, parameters: dict):
        self.name = name
        self.description = description
        self.parameters = parameters  # Cấu trúc JSON Schema

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Hàm thực thi công cụ và trả về kết quả dạng chuỗi (string)"""
        pass

    def to_openai_format(self) -> dict:
        """Chuyển đổi sang định dạng mà OpenAI API yêu cầu"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }