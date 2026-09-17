from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .definition import CapabilityDefinition, CapabilityKind
from .implementation import CapabilityExecutionLocation, CapabilityOwnerType


class CapabilityRegistration(BaseModel):
    """Registration request for a logical capability and one implementation."""

    model_config = ConfigDict(extra="forbid")

    definition: CapabilityDefinition
    kind: CapabilityKind = CapabilityKind.TOOL
    location: CapabilityExecutionLocation
    driver_kind: str

    owner_type: CapabilityOwnerType = CapabilityOwnerType.SYSTEM
    owner_id: Optional[str] = None
    connection_id: Optional[str] = None

    implementation_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ClientCapabilityRegistration(BaseModel):
    """Handshake payload used when a client advertises local capabilities."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str
    client_id: str
    owner_id: str
    capabilities: List[CapabilityRegistration] = Field(default_factory=list)
