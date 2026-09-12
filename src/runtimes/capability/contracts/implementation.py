from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from .definition import CapabilityDefinition


class CapabilityExecutionLocation(str, Enum):
    """Physical execution location of one capability implementation."""

    SERVER = "SERVER"
    CLIENT = "CLIENT"
    MCP = "MCP"
    DECLARATIVE = "DECLARATIVE"


class CapabilityImplementationState(str, Enum):
    """Lifecycle/availability state of one concrete implementation."""

    REGISTERED = "REGISTERED"
    ENABLED = "ENABLED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"
    REMOVED = "REMOVED"


class CapabilityOwnerType(str, Enum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    CLIENT = "CLIENT"
    WORKSPACE = "WORKSPACE"


class CapabilityImplementation(BaseModel):
    """Concrete execution binding for a logical CapabilityDefinition.

    The implementation identity is intentionally independent from
    ``capability_id`` so one capability can have multiple server/client
    implementations at the same time.
    """

    model_config = ConfigDict(extra="forbid")

    implementation_id: str
    capability_id: str
    version: str = "1.0"
    location: CapabilityExecutionLocation
    driver_kind: str

    owner_id: Optional[str] = None
    connection_id: Optional[str] = None

    owner_type: CapabilityOwnerType = CapabilityOwnerType.SYSTEM

    state: CapabilityImplementationState = (
        CapabilityImplementationState.REGISTERED
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_definition(
        cls,
        definition: CapabilityDefinition,
        *,
        implementation_id: str,
        location: CapabilityExecutionLocation,
        driver_kind: str,
        owner_id: Optional[str] = None,
        connection_id: Optional[str] = None,
        owner_type: CapabilityOwnerType = CapabilityOwnerType.SYSTEM,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "CapabilityImplementation":
        return cls(
            implementation_id=implementation_id,
            capability_id=definition.capability_id,
            version=definition.version,
            location=location,
            driver_kind=driver_kind,
            owner_id=owner_id,
            connection_id=connection_id,
            owner_type=owner_type,
            metadata=metadata or {},
        )