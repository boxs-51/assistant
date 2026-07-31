# src/kernel/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Callable, Awaitable
from pydantic import BaseModel, Field
import uuid
import time

class BaseRuntime(ABC):
    def __init__(self, name: str):
        self.name = name
        self._is_initialized = False
        self._is_running = False

    @abstractmethod
    async def initialize(self, context: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

