"""Acknowledged Kafka publication; no logging-only success fallback."""
import asyncio
import json
import os
import ssl
import math


class KafkaPublisher:
    def __init__(self, bootstrap_servers='localhost:9092', *, producer_factory=None,
                 security_protocol='PLAINTEXT', ssl_context=None, sasl_mechanism='PLAIN',
                 sasl_username=None, sasl_password=None, timeout=30.0, client_id='python-inventory-outbox'):
        if not bootstrap_servers or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Kafka servers and a positive timeout are required')
        if security_protocol not in ('PLAINTEXT', 'SSL', 'SASL_PLAINTEXT', 'SASL_SSL'):
            raise ValueError('Invalid Kafka security protocol')
        if security_protocol.startswith('SASL') and (not sasl_username or not sasl_password):
            raise ValueError('Kafka SASL credentials are required')
        if security_protocol.endswith('SSL') and ssl_context is None:
            raise ValueError('Kafka TLS context is required')
        if sasl_mechanism not in ('PLAIN', 'SCRAM-SHA-256', 'SCRAM-SHA-512'):
            raise ValueError('Unsupported Kafka SASL mechanism')
        self.bootstrap_servers, self.timeout = bootstrap_servers, timeout
        self.producer_factory, self.producer = producer_factory, None
        self.options = {'bootstrap_servers': bootstrap_servers, 'client_id': client_id,
            'enable_idempotence': True, 'acks': 'all', 'security_protocol': security_protocol,
            'request_timeout_ms': int(timeout * 1000)}
        if ssl_context:
            self.options['ssl_context'] = ssl_context
        if security_protocol.startswith('SASL'):
            self.options.update(sasl_mechanism=sasl_mechanism, sasl_plain_username=sasl_username,
                                sasl_plain_password=sasl_password)

    @classmethod
    def from_env(cls, *, timeout=30.0):
        servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', '').strip()
        if not servers:
            raise ValueError('Set KAFKA_BOOTSTRAP_SERVERS before enabling event delivery')
        protocol = os.getenv('KAFKA_SECURITY_PROTOCOL', 'PLAINTEXT').upper()
        context = None
        if protocol.endswith('SSL'):
            context = ssl.create_default_context(cafile=os.getenv('KAFKA_SSL_CA_FILE') or None)
            cert = os.getenv('KAFKA_SSL_CERT_FILE')
            if cert:
                context.load_cert_chain(cert, os.getenv('KAFKA_SSL_KEY_FILE'), os.getenv('KAFKA_SSL_KEY_PASSWORD'))
        return cls(servers, timeout=timeout, security_protocol=protocol, ssl_context=context,
            sasl_mechanism=os.getenv('KAFKA_SASL_MECHANISM', 'PLAIN'),
            sasl_username=os.getenv('KAFKA_SASL_USERNAME'), sasl_password=os.getenv('KAFKA_SASL_PASSWORD'),
            client_id=os.getenv('KAFKA_CLIENT_ID', 'python-inventory-outbox'))

    async def start(self):
        if self.producer is not None:
            return
        factory = self.producer_factory
        if factory is None:
            from aiokafka import AIOKafkaProducer
            factory = AIOKafkaProducer
        producer = factory(**self.options)
        try:
            await asyncio.wait_for(producer.start(), self.timeout)
        except BaseException:
            try:
                await asyncio.wait_for(producer.stop(), self.timeout)
            except Exception:
                pass
            raise
        self.producer = producer

    async def stop(self):
        producer, self.producer = self.producer, None
        if producer is not None:
            await asyncio.wait_for(producer.stop(), self.timeout)

    async def publish(self, topic, key, message, *, headers=None):
        if self.producer is None:
            raise RuntimeError('Kafka publisher has not started')
        encoded = json.dumps(message, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        return await asyncio.wait_for(self.producer.send_and_wait(topic, encoded,
            key=key.encode('utf-8'), headers=headers or []), self.timeout)
