from typing import List

from mcp import StdioServerParameters


class McpTransportFactory:
    """Creates MCP transport configuration without owning Gateway logic."""

    @staticmethod
    def create_stdio_params(command: str, args: List[str]) -> StdioServerParameters:
        import os

        return StdioServerParameters(
            command=command,
            args=list(args),
            env=os.environ.copy(),
        )
