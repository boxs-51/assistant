from mcp import StdioServerParameters

from typing import List

class McpTransportFactory:
    """Factory phụ trách khởi tạo cấu hình Transport cho MCP (Mục 11)."""
    @staticmethod
    def create_stdio_params(command: str, args: List[str]) -> StdioServerParameters:
        import os
        return StdioServerParameters(
            command=command,
            args=args,
            env=os.environ.copy()
        )