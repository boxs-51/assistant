from pydantic import BaseModel, Field
import time
from typing import Optional

from .providers.base.provider.provider import BaseProvider

class ProviderEntry(BaseModel):
    """
    Đóng gói một provider instance cùng với trạng thái và metadata của nó.
    Đây là đơn vị cơ sở được quản lý bởi ProviderRegistry.
    """
    provider: BaseProvider
    healthy: bool = True
    weight: int = 100
    priority: int = 10
    registered_at: float = Field(default_factory=time.time)
    last_check_at: Optional[float] = None

    class Config:
        arbitrary_types_allowed = True # Cho phép Pydantic chứa đối tượng BaseProvider