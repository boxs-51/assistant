# e:\assistant\src
#runtime\kernel\manifest.py - Part of the AI Runtime Kernel

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class RuntimeManifest(BaseModel):
    """
    Pydantic model representing the manifest for a runtime.
    
    This manifest (`runtime.yaml`) describes the runtime's identity, dependencies,
    and other metadata needed by the Kernel for discovery and management.
    """
    
    id: str = Field(..., description="Unique identifier for the runtime (e.g., 'com.gemini.provider_runtime').")
    name: str = Field(..., description="Human-readable name of the runtime.")
    version: str = Field(..., description="Semantic version of the runtime (e.g., '1.0.0').")
    
    dependencies: Optional[List[str]] = Field(default_factory=list, description="List of runtime IDs that this runtime depends on.")
    
    exports: Optional[List[str]] = Field(default_factory=list, description="List of services or APIs this runtime exports for other runtimes.")
    
    permissions: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Security permissions required by the runtime (e.g., 'filesystem', 'network').")
    
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional arbitrary metadata about the runtime.")
