import asyncio
import json
from unittest.mock import AsyncMock, Mock
import pytest
from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.infrastructure.messaging.settings import OutboxSettings, outbox_enabled


def producer():
    return Mock(start=AsyncMock(), stop=AsyncMock(), send_and_wait=AsyncMock(return_value='ack'))


@pytest.mark.asyncio
async def test_publisher_waits_for_ack_and_sends_exact_envelope_headers_and_key(caplog):
    client = producer()
    factory = Mock(return_value=client)
    publisher = KafkaPublisher('test:9092', producer_factory=factory)
    await publisher.start()
    await publisher.start()
    assert factory.call_args.kwargs['acks'] == 'all'
    assert factory.call_args.kwargs['enable_idempotence'] is True
    message = {'eventId': 'event-1', 'payload': {'name': 'retail'}}
    headers = [('event-id', b'event-1')]
    assert await publisher.publish('inventory.Stock.events', 'tenant:stock', message, headers=headers) == 'ack'
    args, kwargs = client.send_and_wait.call_args
    assert args[0] == 'inventory.Stock.events' and json.loads(args[1]) == message
    assert kwargs == {'key': b'tenant:stock', 'headers': headers}
    assert 'retail' not in caplog.text
    await publisher.stop()
    await publisher.stop()
    client.start.assert_awaited_once()
    client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_failure_is_not_reported_as_success_and_failed_start_cleans_up():
    client = producer()
    client.start.side_effect = RuntimeError('Unavailable')
    publisher = KafkaPublisher(producer_factory=Mock(return_value=client))
    with pytest.raises(RuntimeError):
        await publisher.start()
    client.stop.assert_awaited_once()
    assert publisher.producer is None
    with pytest.raises(RuntimeError, match='not started'):
        await publisher.publish('topic', 'key', {})
    client.start.side_effect = None
    await publisher.start()
    client.send_and_wait.side_effect = RuntimeError('No acknowledgment')
    with pytest.raises(RuntimeError, match='acknowledgment'):
        await publisher.publish('topic', 'key', {})
    await publisher.stop()


@pytest.mark.asyncio
async def test_publish_has_a_bounded_timeout():
    client = producer()
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()
    client.send_and_wait.side_effect = blocked
    publisher = KafkaPublisher(producer_factory=Mock(return_value=client), timeout=0.01)
    await publisher.start()
    with pytest.raises(TimeoutError):
        await publisher.publish('topic', 'key', {})
    await publisher.stop()


def test_required_broker_credentials_and_delivery_configuration(monkeypatch):
    monkeypatch.delenv('KAFKA_BOOTSTRAP_SERVERS', raising=False)
    with pytest.raises(ValueError, match='BOOTSTRAP'):
        KafkaPublisher.from_env()
    monkeypatch.setenv('KAFKA_BOOTSTRAP_SERVERS', 'broker:9093')
    monkeypatch.setenv('KAFKA_SECURITY_PROTOCOL', 'SASL_SSL')
    monkeypatch.setenv('KAFKA_SASL_USERNAME', 'service')
    monkeypatch.setenv('KAFKA_SASL_PASSWORD', 'test-only')
    configured = KafkaPublisher.from_env()
    assert configured.options['ssl_context'].check_hostname
    assert configured.options['sasl_plain_username'] == 'service'
    monkeypatch.setenv('OUTBOX_ENABLED', 'maybe')
    with pytest.raises(ValueError):
        outbox_enabled()
    for settings in ({'lease_seconds': 30}, {'max_attempts': 0}, {'tenant_ids': ()}, {'topic_prefix': '../bad'}, {'lease_seconds': float('nan')}):
        with pytest.raises(ValueError):
            OutboxSettings(**settings)
