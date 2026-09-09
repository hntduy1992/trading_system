"""
Event Bus Interface (Port) - In-memory / Async PubSub for decoupled inter-service communication
"""
from abc import ABC, abstractmethod
from typing import Callable, Any, Coroutine, Dict, List

class IEventBus(ABC):
    @abstractmethod
    def subscribe(self, topic: str, handler: Callable[[Any], Coroutine[Any, Any, None]]) -> None:
        """Register an async callback for a specific event topic."""
        pass

    @abstractmethod
    async def publish(self, topic: str, data: Any) -> None:
        """Publish event data to all registered listeners asynchronously."""
        pass
