from abc import ABC, abstractmethod
from typing import Any

class ChatProvider(ABC):
    # =========================
    # Chat / Completion
    # =========================
    @abstractmethod
    async def chat(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def chat_stream(self, **kwargs) -> Any: raise NotImplementedError
