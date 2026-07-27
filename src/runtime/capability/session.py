from dataclasses import dataclass, field
from typing import Dict, Any
from uuid import UUID, uuid4


@dataclass
class CapabilitySession:
    """
    Represents the execution context and state for a single capability execution.
    """

    session_id: UUID = field(default_factory=uuid4)
    capability_name: str
    state: Dict[str, Any] = field(default_factory=dict)
