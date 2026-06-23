from src.tools.base import BaseTool
from src.tools.internet_tool import FetchWebTool

class ToolRegistry:
    def __init__(self):
        self._tools = {}
        # Đăng ký sẵn các tool có trong hệ thống
        self.register(FetchWebTool())

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