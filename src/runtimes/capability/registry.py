# src/runtime/runtimes/capability/registry.py
import structlog
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Dict, List, Optional

from .contracts.definition import CapabilityDefinition
from .drivers.base import BaseCapabilityDriver

logger = structlog.get_logger(__name__)


class CapabilityState(str, Enum):
    DISCOVERED = "DISCOVERED"
    REGISTERED = "REGISTERED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    REMOVED = "REMOVED"


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    definition: CapabilityDefinition
    driver: Optional[BaseCapabilityDriver] = None
    state: CapabilityState = CapabilityState.DISCOVERED

    @property
    def id(self) -> str:
        return self.definition.capability_id

    @property
    def executable(self) -> bool:
        return self.driver is not None and self.state not in {
            CapabilityState.DISABLED,
            CapabilityState.UNAVAILABLE,
            CapabilityState.REMOVED,
        }


class CapabilityRegistry:
    def __init__(self):
        self._records: Dict[str, CapabilityRecord] = {}
        self._lock = RLock()

    def register_capability(self, driver: BaseCapabilityDriver):
        definition = driver.definition
        capability_id = definition.capability_id
        with self._lock:
            existing = self._records.get(capability_id)
            if existing and existing.driver is not None:
                logger.warning(
                    "Replacing existing executable capability",
                    capability_id=capability_id,
                )
            record = CapabilityRecord(
                definition=definition,
                driver=driver,
                state=CapabilityState.ENABLED,
            )
            self._records[capability_id] = record
        logger.info("Capability registered", capability_id=capability_id)
        return record

    def set_state(
        self,
        name: str,
        state: CapabilityState,
    ) -> Optional[CapabilityRecord]:
        """Atomically update one capability lifecycle/availability state."""
        with self._lock:
            record = self._records.get(name)
            if record is None:
                return None
            updated = CapabilityRecord(
                definition=record.definition,
                driver=record.driver,
                state=state,
            )
            self._records[name] = updated
            return updated

    def register_definition(self, definition: CapabilityDefinition):
        """Compatibility/discovery registration without claiming executability."""
        capability_id = definition.capability_id
        with self._lock:
            existing = self._records.get(capability_id)
            if existing and existing.driver is not None:
                return existing
            record = CapabilityRecord(
                definition=definition,
                driver=None,
                state=CapabilityState.DISCOVERED,
            )
            self._records[capability_id] = record
        logger.info("Capability definition discovered", capability_id=capability_id)
        return record

    def get_driver(self, name: str) -> Optional[BaseCapabilityDriver]:
        record = self.get(name)
        return record.driver if record and record.executable else None

    def get_all_drivers(self) -> List[BaseCapabilityDriver]:
        with self._lock:
            return [
                record.driver
                for record in self._records.values()
                if record.executable and record.driver is not None
            ]

    def get_definition(self, name: str) -> Optional[CapabilityDefinition]:
        record = self.get(name)
        return record.definition if record else None

    def get(self, name: str) -> Optional[CapabilityRecord]:
        with self._lock:
            return self._records.get(name)

    def list_records(self) -> List[CapabilityRecord]:
        with self._lock:
            return list(self._records.values())

    def unregister(self, name: str) -> Optional[CapabilityRecord]:
        with self._lock:
            record = self._records.pop(name, None)
        if record:
            logger.info("Capability unregistered", capability_id=name)
        return record