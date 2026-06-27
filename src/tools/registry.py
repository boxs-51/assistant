import os
import importlib
import inspect
from src.tools.base import BaseTool

class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def discover_and_register(self, directory="src/tools"):
        """Tự động quét, tải và đăng ký tất cả các Tool trong một thư mục."""
        for filename in os.listdir(directory):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]
                module_path = f"{directory.replace('/', '.')}.{module_name}"
                
                try:
                    module = importlib.import_module(module_path)
                    for name, cls in inspect.getmembers(module, inspect.isclass):
                        # Đảm bảo class được định nghĩa trong module này (không phải import)
                        # và là một subclass của BaseTool, nhưng không phải chính BaseTool
                        if issubclass(cls, BaseTool) and cls is not BaseTool and cls.__module__ == module.__name__:
                            tool_instance = cls()
                            self.register(tool_instance)
                except Exception as e:
                    print(f"⚠️ [ToolRegistry] Lỗi khi tải tool từ file {filename}: {e}")
                    
    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_all_tools(self) -> dict:
        return self._tools

    def get_openai_tools_schema(self) -> list:
        """Trả về danh sách schema cho OpenAI"""
        return [tool.to_openai_format() for tool in self._tools.values()]

    def get_local_tools_prompt(self) -> str:
        """Tạo chuỗi text mô tả công cụ cho Local LLM ép format"""
        prompt_parts = []
        for tool in self._tools.values():
            prompt_parts.append(f"- Tên: {tool.name}\n  Mô tả: {tool.description}\n  Tham số: {tool.parameters['properties']}")
        return "\n".join(prompt_parts)

    def execute_tool(self, name: str, arguments: dict) -> str:
        """Tìm và chạy Tool dựa trên tên và tham số truyền vào"""
        tool = self._tools.get(name)
        if not tool:
            return f"Lỗi: Không tìm thấy công cụ nào có tên '{name}'"
        print(f"-> [Hệ thống] Đang kích hoạt Tool '{name}' với tham số: {arguments}")
        return tool.execute(**arguments)