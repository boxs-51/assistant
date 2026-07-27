# e:\assistant\src
#runtime\kernel\kernel.py - Part of the AI Runtime Kernel

import logging
from typing import Optional

from .registry import RuntimeRegistry, RuntimeRecord
from .lifecycle import LifecycleState
from .runtime import Runtime


class RuntimeKernel:
    """
    The central coordinator for the AI Runtime Backend.
    
    The Kernel is responsible for managing the lifecycle of all registered
    runtimes, providing them with necessary context, and orchestrating their
    startup and shutdown procedures.
    """
    
    def __init__(self) -> None:
        self.registry = RuntimeRegistry()
        # Other core services like EventBus, ConfigManager would be initialized here
    
    def get_runtime(self, runtime_id: str) -> Runtime:
        """
        Retrieves a running runtime instance by its ID.

        Args:
            runtime_id: The unique identifier of the runtime.

        Returns:
            The instance of the requested runtime.

        Raises:
            RuntimeError: If the runtime is not found or not in a RUNNING state.
        """
        record = self.registry.get_record(runtime_id)
        if not record:
            raise RuntimeError(f"Runtime with ID '{runtime_id}' not found in registry.")
        
        if record.state != LifecycleState.RUNNING:
            raise RuntimeError(f"Runtime '{runtime_id}' is not in a RUNNING state (current: {record.state}).")
            
        return record.instance

    async def startup(self) -> None:
        """
        Starts the kernel and all registered runtimes.
        
        This process involves initializing and then starting each runtime in a
        controlled sequence.
        """
        logger.info("Runtime Kernel starting up...")
        
        runtime_records = await self.registry.get_all_records()
        
        # Initialization phase
        logger.info("Initializing all runtimes...")
        for record in runtime_records:
            await self._initialize_runtime(record)
            
        # Startup phase
        logger.info("Starting all runtimes...")
        for record in runtime_records:
            await self._start_runtime(record)
            
        logger.info("Runtime Kernel startup complete.")

    async def shutdown(self) -> None:
        """
        Shuts down the kernel and all registered runtimes.
        
        This process involves stopping and then disposing of each runtime's
        resources gracefully.
        """
        logger.info("Runtime Kernel shutting down...")
        
        runtime_records = await self.registry.get_all_records()
        
        # Shutdown phase
        logger.info("Stopping all runtimes...")
        for record in reversed(runtime_records): # Stop in reverse order of startup
            await self._stop_runtime(record)

        # Dispose phase
        logger.info("Disposing all runtimes...")
        for record in reversed(runtime_records):
            await self._dispose_runtime(record)

        logger.info("Runtime Kernel shutdown complete.")

    async def _initialize_runtime(self, record: RuntimeRecord) -> None:
        """Handles the initialization of a single runtime, with state tracking."""
        try:
            logger.debug(f"Initializing runtime '{record.manifest.id}'...")
            await self.registry.set_state(record.manifest.id, LifecycleState.INITIALIZING)
            await record.instance.initialize()
            await self.registry.set_state(record.manifest.id, LifecycleState.INITIALIZED)
            logger.info(f"Runtime '{record.manifest.id}' initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize runtime '{record.manifest.id}': {e}", exc_info=True)
            await self.registry.set_state(record.manifest.id, LifecycleState.FAILED)

    async def _start_runtime(self, record: RuntimeRecord) -> None:
        """Handles the startup of a single runtime, with state tracking."""
        if record.state != LifecycleState.INITIALIZED:
            logger.warning(f"Skipping start for runtime '{record.manifest.id}' because it is not in INITIALIZED state (current: {record.state}).")
            return
        
        try:
            logger.debug(f"Starting runtime '{record.manifest.id}'...")
            await self.registry.set_state(record.manifest.id, LifecycleState.STARTING)
            await record.instance.start()
            await self.registry.set_state(record.manifest.id, LifecycleState.RUNNING)
            logger.info(f"Runtime '{record.manifest.id}' started successfully.")
        except Exception as e:
            logger.error(f"Failed to start runtime '{record.manifest.id}': {e}", exc_info=True)
            await self.registry.set_state(record.manifest.id, LifecycleState.FAILED)
            
    async def _stop_runtime(self, record: RuntimeRecord) -> None:
        """Handles the stopping of a single runtime."""
        if record.state != LifecycleState.RUNNING:
            return # Only stop running runtimes
            
        try:
            logger.debug(f"Stopping runtime '{record.manifest.id}'...")
            await self.registry.set_state(record.manifest.id, LifecycleState.STOPPING)
            await record.instance.stop()
            await self.registry.set_state(record.manifest.id, LifecycleState.STOPPED)
            logger.info(f"Runtime '{record.manifest.id}' stopped successfully.")
        except Exception as e:
            logger.error(f"Failed to stop runtime '{record.manifest.id}': {e}", exc_info=True)
            await self.registry.set_state(record.manifest.id, LifecycleState.FAILED)

    async def _dispose_runtime(self, record: RuntimeRecord) -> None:
        """Handles the disposal of a single runtime's resources."""
        if record.state not in (LifecycleState.STOPPED, LifecycleState.FAILED, LifecycleState.INITIALIZED):
             return
             
        try:
            logger.debug(f"Disposing runtime '{record.manifest.id}'...")
            await self.registry.set_state(record.manifest.id, LifecycleState.DISPOSING)
            await record.instance.dispose()
            await self.registry.set_state(record.manifest.id, LifecycleState.DISPOSED)
            logger.info(f"Runtime '{record.manifest.id}' disposed successfully.")
        except Exception as e:
            # Not much we can do here, just log it
            logger.error(f"Failed to dispose runtime '{record.manifest.id}': {e}", exc_info=True)

