import pytest
import logging
from src.infrastructure.messaging.kafka_publisher import KafkaPublisher

def test_kafka_publisher_init_defaults():
    publisher = KafkaPublisher()
    assert publisher.bootstrap_servers == 'localhost:9092'
    assert publisher.producer is None

def test_kafka_publisher_init_custom():
    publisher = KafkaPublisher(bootstrap_servers='kafka.internal:9092')
    assert publisher.bootstrap_servers == 'kafka.internal:9092'
    assert publisher.producer is None

@pytest.mark.asyncio
async def test_kafka_publisher_start(caplog):
    caplog.set_level(logging.INFO)
    publisher = KafkaPublisher(bootstrap_servers='localhost:9092')
    await publisher.start()
    assert "Connecting to Kafka at localhost:9092..." in caplog.text

@pytest.mark.asyncio
async def test_kafka_publisher_stop(caplog):
    caplog.set_level(logging.INFO)
    publisher = KafkaPublisher()
    await publisher.stop()
    assert "Disconnecting from Kafka..." in caplog.text

@pytest.mark.asyncio
async def test_kafka_publisher_publish(caplog):
    caplog.set_level(logging.INFO)
    publisher = KafkaPublisher()
    topic = "test_topic"
    key = "order_123"
    message = {"event": "order_created", "id": 123}

    await publisher.publish(topic, key, message)
    assert "Published to test_topic [Key: order_123]: {\"event\": \"order_created\", \"id\": 123}" in caplog.text
