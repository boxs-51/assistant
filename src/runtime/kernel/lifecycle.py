# e:\assistant\src
#runtime\kernel\lifecycle.py - Part of the AI Runtime Kernel

from enum import Enum

class LifecycleState(Enum):
    """
    Defines the possible lifecycle states of a Runtime instance.
    
    The Runtime Kernel manages the transition between these states.
    """
    
    CREATED = "CREATED"          # The runtime object has been instantiated.
    INITIALIZING = "INITIALIZING"  # The initialize() method has been called.
    INITIALIZED = "INITIALIZED"    # The initialize() method has completed.
    STARTING = "STARTING"        # The start() method has been called.
    RUNNING = "RUNNING"          # The start() method has completed, runtime is operational.
    STOPPING = "STOPPING"        # The stop() method has been called.
    STOPPED = "STOPPED"          # The stop() method has completed.
    DISPOSING = "DISPOSING"      # The dispose() method has been called.
    DISPOSED = "DISPOSED"        # The dispose() method has completed, resources are released.
    FAILED = "FAILED"            # An unrecoverable error occurred.
