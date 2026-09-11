from typing import Iterable, Optional, Protocol

from .contracts.definition import CapabilityDefinition
from .contracts.implementation import CapabilityImplementation


class CapabilityControlPlane(Protocol):
    """Boundary for capability metadata, ownership and routing decisions.

    This protocol is intentionally not an execution interface. Concrete execution
    remains behind CapabilityRuntime/ToolExecutionPort.
    """

    def register_definition(
        self,
        definition: CapabilityDefinition,
    ) -> CapabilityDefinition:
        ...

    def register_implementation(
        self,
        implementation: CapabilityImplementation,
    ) -> CapabilityImplementation:
        ...

    def list_implementations(
        self,
        capability_id: str,
    ) -> Iterable[CapabilityImplementation]:
        ...

    def select_implementation(
        self,
        capability_id: str,
        *,
        owner_id: Optional[str] = None,
        connection_id: Optional[str] = None,
    ) -> CapabilityImplementation:
        ...
