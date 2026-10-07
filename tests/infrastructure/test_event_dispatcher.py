import pytest
from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

from src.domain.events import DomainEvent
from src.infrastructure.event_dispatcher import EventDispatcher

@dataclass
class DummyEventA(DomainEvent):
    data: str = "A"

@dataclass
class DummyEventB(DomainEvent):
    data: str = "B"

@pytest.mark.asyncio
async def test_register_and_dispatch_single_handler():
    dispatcher = EventDispatcher()
    handler = AsyncMock()

    dispatcher.register(DummyEventA, handler)

    event = DummyEventA(data="test_single")
    await dispatcher.dispatch(event)

    handler.assert_awaited_once_with(event)

@pytest.mark.asyncio
async def test_register_and_dispatch_multiple_handlers():
    dispatcher = EventDispatcher()
    handler1 = AsyncMock()
    handler2 = AsyncMock()

    dispatcher.register(DummyEventA, handler1)
    dispatcher.register(DummyEventA, handler2)

    event = DummyEventA(data="test_multiple")
    await dispatcher.dispatch(event)

    handler1.assert_awaited_once_with(event)
    handler2.assert_awaited_once_with(event)

@pytest.mark.asyncio
async def test_dispatch_unregistered_event():
    dispatcher = EventDispatcher()
    handler = AsyncMock()

    dispatcher.register(DummyEventA, handler)

    event_b = DummyEventB(data="unhandled")
    await dispatcher.dispatch(event_b)

    handler.assert_not_awaited()

@pytest.mark.asyncio
async def test_handler_exception_handling():
    dispatcher = EventDispatcher()

    failing_handler = AsyncMock(side_effect=Exception("Handler failure"))
    successful_handler = AsyncMock()

    dispatcher.register(DummyEventA, failing_handler)
    dispatcher.register(DummyEventA, successful_handler)

    event = DummyEventA(data="test_error")

    with patch("src.infrastructure.event_dispatcher.logger") as mock_logger:
        await dispatcher.dispatch(event)

        failing_handler.assert_awaited_once_with(event)
        successful_handler.assert_awaited_once_with(event)
        mock_logger.error.assert_called_once()
        assert "Error handling event DummyEventA: Handler failure" in mock_logger.error.call_args[0][0]

@pytest.mark.asyncio
async def test_dispatch_event_with_no_handlers():
    dispatcher = EventDispatcher()
    event = DummyEventA()
    # Should complete without error
    await dispatcher.dispatch(event)
