from typing import Any, Dict

from ....tool import GatewayToolManager
from ....domain.schemas.identity import Identity
from ..driver import CapabilityDriver
from ..session import CapabilitySession


class ToolDriver(CapabilityDriver):
    """
    A capability driver that executes tools using the legacy GatewayToolManager.
    This acts as an adapter to bridge the new Capability Runtime with the old tool execution logic.
    """

    def __init__(self, tool_manager: GatewayToolManager):
        """
        Initializes the ToolDriver.

        Args:
            tool_manager: An instance of the existing GatewayToolManager.
        """
        self._tool_manager = tool_manager

    async def execute(
        self, session: CapabilitySession, params: Dict[str, Any]
    ) -> Any:
        """
        Executes a tool via the GatewayToolManager.

        The 'params' dictionary is expected to contain:
        - 'tool_name' (str): The name of the tool to execute.
        - 'arguments' (Dict[str, Any]): The arguments for the tool.
        - 'identity' (Identity): The identity of the user/caller.

        Args:
            session: The capability session, containing execution context.
            params: A dictionary containing the necessary parameters for tool execution.

        Returns:
            The result from the tool execution.
            
        Raises:
            KeyError: If required parameters ('tool_name', 'arguments', 'identity')
                      are missing from the `params` dictionary.
        """
        tool_name = params['tool_name']
        arguments = params['arguments']
        identity = params['identity']

        return await self._tool_manager.execute_tool(
            tool_name=tool_name,
            arguments=arguments,
            identity=identity
        )
