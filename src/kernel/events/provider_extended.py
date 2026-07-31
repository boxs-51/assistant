# src/kernel/events/provider_extended.py
from typing import Dict, Any, Optional
from pydantic import BaseModel

# Embeddings
class ExecuteEmbeddingsPayload(BaseModel):
    request_body: Dict[str, Any]

# Models
class GetModelsPayload(BaseModel):
    provider_name: str
    model_id: Optional[str] = None

# Files
class FileOperationPayload(BaseModel):
    action: str  # "list", "upload", "get_metadata", "download", "delete"
    provider_name: str
    file_id: Optional[str] = None
    display_name: Optional[str] = None
    file_bytes: Optional[bytes] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    page_size: Optional[int] = None
    page_token: Optional[str] = None