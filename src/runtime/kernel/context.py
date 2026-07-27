# e:\assistant\src
#runtime\kernel\context.py - Part of the AI Runtime Kernel

from typing import Any, Protocol, Dict

# Forward-declare the Kernel to avoid circular imports
class RuntimeKernel(Protocol):
    # Define methods that context users might need, or just 'pass'
    pass

class RuntimeContext:
    """
    Provides a sandboxed context for each runtime instance.
    
    The Kernel creates and injects this context into each runtime upon
    initialization. It acts as a service locator and provides access to
    shared kernel services, configuration, and other essential utilities
    without giving the runtime access to the Kernel itself.
    """
    
    def __init__(self, kernel: "RuntimeKernel", runtime_id: str):
        self._kernel = kernel
        self.runtime_id = runtime_id
        self._services: Dict[str, Any] = {}

    def register_service(self, name: str, service: Any) -> None:
        """Registers a shared service with this context."""
        if name in self._services:
            # Depending on policy, either raise an error or log a warning
            pass
        self._services[name] = service

    def get_service(self, service_name: str) -> Any:
        """
        Requests a shared service from the context.
        """
        try:
            return self._services[service_name]
        except KeyError:
            # In a more complex system, this could fall back to a parent context
            raise KeyError(f"Service '{service_name}' not found in runtime context '{self.runtime_id}'.")
