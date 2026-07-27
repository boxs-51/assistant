"""
Capability Registry
"""
from typing import Dict, Optional
from .driver import CapabilityDriver


class CapabilityRegistry:
    """
    Discovers and registers all available capabilities and their drivers.
    """

    def __init__(self):
        self._drivers: Dict[str, CapabilityDriver] = {}

    def register(self, capability_name: str, driver: CapabilityDriver):
        """
        Registers a driver for a given capability.

        Args:
            capability_name: The name of the capability.
            driver: The driver that executes the capability.
        """
        if capability_name in self._drivers:
            # Handle potential conflicts or updates if necessary
            print(f"Warning: Overwriting driver for capability '{capability_name}'")
        self._drivers[capability_name] = driver

    def get_driver_for_capability(self, capability_name: str) -> Optional[CapabilityDriver]:
        """
        Finds the driver for a given capability.

        Args:
            capability_name: The name of the capability.

        Returns:
            The corresponding driver, or None if not found.
        """
        return self._drivers.get(capability_name)
