import asyncio
from typing import Dict, List, Callable, Any
from ..schemas.runtime.runtime import RuntimeEvent

class InternalEventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    async def subscribe(self, event_type: str, handler: Callable[[RuntimeEvent], Any]):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def publish(self, event: RuntimeEvent):
        if event.event_type in self._subscribers:
            # Chạy tất cả các subscriber bất đồng bộ (Fan-out)
            tasks = [
                asyncio.create_task(handler(event)) 
                for handler in self._subscribers[event.event_type]
            ]
            await asyncio.gather(*tasks, return_exceptions=True)