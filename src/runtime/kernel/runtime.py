# e:\assistant\src
#runtime\kernel
#runtime.py - Part of the AI Runtime Kernel

from abc import ABC, abstractmethod

class Runtime(ABC):
    """
    Abstract base class for all Runtimes in the system.
    
    This class defines the standard lifecycle interface that the Runtime Kernel
    will manage. Each runtime component (e.g., CapabilityRuntime, ProviderRuntime)
    must inherit from this class and implement the required methods.
    """

    @abstractmethod
    async def initialize(self, **kwargs) -> None:
        """
        Initializes the runtime's resources, dependencies, and internal state.
        This method is called once by the kernel before starting the runtime.
        """
        pass

    @abstractmethod
    async def start(self) -> None:
        """
        Starts the runtime's main operations.
        For example, subscribing to events, starting background tasks, etc.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Gracefully stops the runtime's operations.
        Should ensure that all ongoing tasks are completed or safely terminated.
        """
        pass

    @abstractmethod
    async def dispose(self) -> None:
        """
        Releases all resources held by the runtime.
        This is the final step in the runtime's lifecycle.
        """
        pass
