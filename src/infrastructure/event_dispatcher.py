import asyncio
import logging
from typing import Callable, Awaitable, List, Dict, Type
from src.domain.events import DomainEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[DomainEvent], Awaitable[None]]

class EventDispatcher:
    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = {}

    def register(self, event_type: Type[DomainEvent], handler: EventHandler):
        event_name = event_type.__name__
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)

    async def dispatch(self, event: DomainEvent):
        event_name = event.event_name
        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Error handling event {event_name}: {e}")
                
# Singleton dispatcher for simplicity, though DI is preferred
dispatcher = EventDispatcher()
