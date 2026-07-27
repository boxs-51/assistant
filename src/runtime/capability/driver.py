from abc import ABC, abstractmethod
from typing import Dict, Any

# Forward reference for type hinting to avoid circular import
if 'CapabilitySession' not in globals():
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from .session import CapabilitySession


class CapabilityDriver(ABC):
    """
    Abstract base class for a capability driver.
    A driver is responsible for the actual execution of a capability.
    """

    @abstractmethod
    async def execute(
        self, session: "CapabilitySession", params: Dict[str, Any]
    ) -> Any:
        """
        Executes the capability.

        Args:
            session: The capability session, containing execution context.
            params: The parameters for the capability execution.

        Returns:
            The result of the capability execution.
        """
        raise NotImplementedError
