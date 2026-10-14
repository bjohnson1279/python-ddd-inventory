from unittest.mock import AsyncMock
import pytest
import src.presentation.main as main


@pytest.mark.asyncio
async def test_enabled_lifespan_starts_and_stops_delivery_even_when_request_scope_fails(monkeypatch):
    monkeypatch.setenv('OUTBOX_ENABLED', 'true')
    publisher, worker = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main, 'build_event_delivery', lambda: (publisher, worker))
    with pytest.raises(RuntimeError, match='Application scope'):
        async with main.lifespan(main.app):
            publisher.start.assert_awaited_once()
            worker.start.assert_awaited_once()
            assert main.app.state.outbox_worker is worker
            raise RuntimeError('Application scope failed')
    worker.stop.assert_awaited_once()
    publisher.stop.assert_awaited_once()
    assert main.app.state.outbox_worker is None


@pytest.mark.asyncio
async def test_startup_failure_cleans_resources_and_is_not_healthy(monkeypatch):
    monkeypatch.setenv('OUTBOX_ENABLED', 'true')
    publisher, worker = AsyncMock(), AsyncMock()
    publisher.start.side_effect = RuntimeError('Broker unavailable')
    monkeypatch.setattr(main, 'build_event_delivery', lambda: (publisher, worker))
    with pytest.raises(RuntimeError):
        async with main.lifespan(main.app):
            pytest.fail('Failed broker startup must not enter serving scope')
    worker.start.assert_not_awaited()
    publisher.stop.assert_awaited_once()
    assert (await main.health_check()).status_code == 503


@pytest.mark.asyncio
async def test_disabled_delivery_does_not_claim_or_connect_and_is_reported(monkeypatch):
    monkeypatch.setenv('OUTBOX_ENABLED', 'false')
    def unexpected():
        raise AssertionError('Disabled delivery must not connect')
    monkeypatch.setattr(main, 'build_event_delivery', unexpected)
    async with main.lifespan(main.app):
        assert main.app.state.outbox_worker is None
        assert b'"enabled":false' in (await main.health_check()).body
