"""
Async In-Memory Event Bus Implementation
Provides ultra-low latency (<1ms) pub/sub on a single host
"""
import asyncio
from typing import Dict, List, Callable, Any, Coroutine
from core.domain.interfaces.event_bus import IEventBus

class AsyncEventBus(IEventBus):
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], Coroutine[Any, Any, None]]]] = {}

    def subscribe(self, topic: str, handler: Callable[[Any], Coroutine[Any, Any, None]]) -> None:
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)

    async def publish(self, topic: str, data: Any) -> None:
        if topic in self._subscribers:
            tasks = [asyncio.create_task(handler(data)) for handler in self._subscribers[topic]]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
