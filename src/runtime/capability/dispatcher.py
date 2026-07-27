"""
Capability Dispatcher
"""
from typing import Any, Dict

from .driver import CapabilityDriver
from .registry import CapabilityRegistry
from .session import CapabilitySession


class CapabilityDispatcher:
    """
    Dispatches capability execution requests to the appropriate driver.
    """

    def __init__(self, registry: CapabilityRegistry):
        """
        Initializes the dispatcher with a capability registry.

        Args:
            registry: The registry to use for finding capability drivers.
        """
        self._registry = registry

    async def dispatch(
        self, capability_name: str, params: Dict[str, Any]
    ) -> Any:
        """
        Finds the correct driver for the given capability, creates a session,
        and executes the capability.

        Args:
            capability_name: The name of the capability to execute.
            params: The parameters for the capability execution.

        Returns:
            The result of the capability execution.
        
        Raises:
            ValueError: If no driver is found for the specified capability.
        """
        print(f"Dispatching capability '{capability_name}'...")

        driver: CapabilityDriver = self._registry.get_driver_for_capability(
            capability_name
        )
        if not driver:
            raise ValueError(f"No driver found for capability '{capability_name}'")

        # Create a new session for this execution
        session = CapabilitySession(capability_name=capability_name)

        result = await driver.execute(session, params)
        print(f"Capability '{capability_name}' executed successfully.")
        return result

