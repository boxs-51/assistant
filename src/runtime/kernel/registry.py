# e:\assistant\src
#runtime\kernel
#registry.py - Part of the AI Runtime Kernel

import asyncio
from typing import Dict, Optional

from .runtime import Runtime
from .lifecycle import LifecycleState
from .manifest import RuntimeManifest

class RuntimeRecord:
    """A record to hold all information about a single runtime instance."""
    def __init__(self, manifest: RuntimeManifest, instance: Runtime):
        self.manifest = manifest
        self.instance = instance
        self.state: LifecycleState = LifecycleState.CREATED
        self.lock = asyncio.Lock()

class RuntimeRegistry:
    """
    Manages the collection of all runtimes in the system.
    
    The registry acts as a central repository for the Kernel to discover,
    access, and manage the state of each runtime.
    """
    
    def __init__(self):
        self._runtimes: Dict[str, RuntimeRecord] = {}
        self._lock = asyncio.Lock()

    async def register(self, manifest: RuntimeManifest, instance: Runtime) -> bool:
        """
        Registers a new runtime instance.
        
        Args:
            manifest: The runtime's manifest data.
            instance: The actual runtime object.
            
        Returns:
            True if registration was successful, False if a runtime with the same ID already exists.
        """
        async with self._lock:
            if manifest.id in self._runtimes:
                # Log an error or warning here
                return False
            
            record = RuntimeRecord(manifest, instance)
            self._runtimes[manifest.id] = record
            return True

    async def get_record(self, runtime_id: str) -> Optional[RuntimeRecord]:
        """Retrieves the record for a given runtime ID."""
        async with self._lock:
            return self._runtimes.get(runtime_id)

    async def get_all_records(self) -> list[RuntimeRecord]:
        """Returns a list of all runtime records."""
        async with self._lock:
            return list(self._runtimes.values())

    async def set_state(self, runtime_id: str, state: LifecycleState) -> bool:
        """
        Updates the lifecycle state of a specific runtime.
        This should only be called by the Kernel's lifecycle manager.
        """
        record = await self.get_record(runtime_id)
        if record:
            async with record.lock:
                record.state = state
            return True
        return False
