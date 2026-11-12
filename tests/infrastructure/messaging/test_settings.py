from dataclasses import FrozenInstanceError
import pytest
from src.infrastructure.messaging.settings import OutboxSettings, outbox_enabled


class TestOutboxEnabled:
    def test_default_is_false(self, monkeypatch):
        monkeypatch.delenv('OUTBOX_ENABLED', raising=False)
        assert outbox_enabled() is False

    @pytest.mark.parametrize('value,expected', [
        ('true', True),
        ('True', True),
        ('TRUE', True),
        ('false', False),
        ('False', False),
        ('FALSE', False),
    ])
    def test_valid_boolean_values(self, monkeypatch, value, expected):
        monkeypatch.setenv('OUTBOX_ENABLED', value)
        assert outbox_enabled() is expected

    @pytest.mark.parametrize('value', [
        'yes',
        'no',
        '1',
        '0',
        'maybe',
        '',
        'disabled',
        'enabled',
    ])
    def test_invalid_values_raise_value_error(self, monkeypatch, value):
        monkeypatch.setenv('OUTBOX_ENABLED', value)
        with pytest.raises(ValueError, match='OUTBOX_ENABLED must be true or false'):
            outbox_enabled()


class TestOutboxSettings:
    def test_default_initialization(self):
        settings = OutboxSettings()
        assert settings.poll_interval == 2.0
        assert settings.batch_size == 50
        assert settings.lease_seconds == 60.0
        assert settings.publish_timeout == 30.0
        assert settings.max_attempts == 5
        assert settings.retry_base == 2.0
        assert settings.retry_cap == 300.0
        assert settings.topic_prefix == 'inventory'
        assert settings.tenant_ids is None

    def test_immutable_instance(self):
        settings = OutboxSettings()
        with pytest.raises(FrozenInstanceError):
            settings.batch_size = 100

    @pytest.mark.parametrize('poll_interval,batch_size', [
        (0.01, 1),
        (300.0, 1000),
        (2.0, 50),
    ])
    def test_valid_poll_interval_and_batch_size(self, poll_interval, batch_size):
        settings = OutboxSettings(poll_interval=poll_interval, batch_size=batch_size)
        assert settings.poll_interval == poll_interval
        assert settings.batch_size == batch_size

    @pytest.mark.parametrize('poll_interval,batch_size', [
        (0.009, 50),
        (300.01, 50),
        (-1.0, 50),
        (0.0, 50),
        (2.0, 0),
        (2.0, -5),
        (2.0, 1001),
    ])
    def test_invalid_poll_interval_or_batch_size_raises(self, poll_interval, batch_size):
        with pytest.raises(ValueError, match='Invalid outbox poll interval or batch size'):
            OutboxSettings(poll_interval=poll_interval, batch_size=batch_size)

    @pytest.mark.parametrize('publish_timeout,lease_seconds', [
        (0.01, 5.02),
        (30.0, 35.01),
        (30.0, 3600.0),
        (300.0, 305.01),
    ])
    def test_valid_publish_timeout_and_lease_seconds(self, publish_timeout, lease_seconds):
        settings = OutboxSettings(publish_timeout=publish_timeout, lease_seconds=lease_seconds)
        assert settings.publish_timeout == publish_timeout
        assert settings.lease_seconds == lease_seconds

    @pytest.mark.parametrize('publish_timeout,lease_seconds', [
        (0.0, 60.0),
        (-1.0, 60.0),
        (300.01, 400.0),
        (30.0, 35.0),
        (30.0, 30.0),
        (30.0, 20.0),
        (30.0, 3600.01),
    ])
    def test_invalid_publish_timeout_or_lease_seconds_raises(self, publish_timeout, lease_seconds):
        with pytest.raises(ValueError, match='Outbox lease must exceed publish timeout by more than five seconds'):
            OutboxSettings(publish_timeout=publish_timeout, lease_seconds=lease_seconds)

    @pytest.mark.parametrize('max_attempts,retry_base,retry_cap', [
        (1, 0.01, 0.01),
        (100, 2.0, 86400.0),
        (5, 10.0, 100.0),
    ])
    def test_valid_retry_settings(self, max_attempts, retry_base, retry_cap):
        settings = OutboxSettings(max_attempts=max_attempts, retry_base=retry_base, retry_cap=retry_cap)
        assert settings.max_attempts == max_attempts
        assert settings.retry_base == retry_base
        assert settings.retry_cap == retry_cap

    @pytest.mark.parametrize('max_attempts,retry_base,retry_cap', [
        (0, 2.0, 300.0),
        (101, 2.0, 300.0),
        (-1, 2.0, 300.0),
        (5, 0.0, 300.0),
        (5, -1.0, 300.0),
        (5, 301.0, 300.0),
        (5, 2.0, 86400.01),
    ])
    def test_invalid_retry_settings_raises(self, max_attempts, retry_base, retry_cap):
        with pytest.raises(ValueError, match='Invalid outbox retry settings'):
            OutboxSettings(max_attempts=max_attempts, retry_base=retry_base, retry_cap=retry_cap)

    @pytest.mark.parametrize('topic_prefix', [
        'a',
        'A',
        '0',
        'inventory',
        'inventory.events',
        'inventory-service_01',
        'a' * 120,
    ])
    def test_valid_topic_prefix(self, topic_prefix):
        settings = OutboxSettings(topic_prefix=topic_prefix)
        assert settings.topic_prefix == topic_prefix

    @pytest.mark.parametrize('topic_prefix', [
        '',
        '.inventory',
        '-inventory',
        '_inventory',
        'inventory$events',
        'inventory/events',
        'a' * 121,
    ])
    def test_invalid_topic_prefix_raises(self, topic_prefix):
        with pytest.raises(ValueError, match='Invalid outbox Kafka topic prefix'):
            OutboxSettings(topic_prefix=topic_prefix)

    @pytest.mark.parametrize('tenant_ids', [
        None,
        ('tenant-1',),
        ('tenant_1', 'TENANT-2', 't' * 100),
    ])
    def test_valid_tenant_ids(self, tenant_ids):
        settings = OutboxSettings(tenant_ids=tenant_ids)
        assert settings.tenant_ids == tenant_ids

    @pytest.mark.parametrize('tenant_ids', [
        (),
        ('',),
        ('tenant-1', ''),
        ('tenant@1',),
        ('tenant.1',),
        ('t' * 101,),
    ])
    def test_invalid_tenant_ids_raises(self, tenant_ids):
        with pytest.raises(ValueError, match='Invalid outbox tenant allowlist'):
            OutboxSettings(tenant_ids=tenant_ids)


class TestOutboxSettingsFromEnv:
    def test_from_env_defaults(self, monkeypatch):
        for var in [
            'OUTBOX_POLL_SECONDS',
            'OUTBOX_BATCH_SIZE',
            'OUTBOX_LEASE_SECONDS',
            'OUTBOX_PUBLISH_TIMEOUT_SECONDS',
            'OUTBOX_MAX_ATTEMPTS',
            'OUTBOX_RETRY_BASE_SECONDS',
            'OUTBOX_RETRY_CAP_SECONDS',
            'OUTBOX_TOPIC_PREFIX',
            'OUTBOX_TENANT_IDS',
        ]:
            monkeypatch.delenv(var, raising=False)

        settings = OutboxSettings.from_env()
        assert settings.poll_interval == 2.0
        assert settings.batch_size == 50
        assert settings.lease_seconds == 60.0
        assert settings.publish_timeout == 30.0
        assert settings.max_attempts == 5
        assert settings.retry_base == 2.0
        assert settings.retry_cap == 300.0
        assert settings.topic_prefix == 'inventory'
        assert settings.tenant_ids is None

    def test_from_env_custom_values(self, monkeypatch):
        monkeypatch.setenv('OUTBOX_POLL_SECONDS', '10.5')
        monkeypatch.setenv('OUTBOX_BATCH_SIZE', '100')
        monkeypatch.setenv('OUTBOX_LEASE_SECONDS', '120.0')
        monkeypatch.setenv('OUTBOX_PUBLISH_TIMEOUT_SECONDS', '45.0')
        monkeypatch.setenv('OUTBOX_MAX_ATTEMPTS', '10')
        monkeypatch.setenv('OUTBOX_RETRY_BASE_SECONDS', '5.0')
        monkeypatch.setenv('OUTBOX_RETRY_CAP_SECONDS', '600.0')
        monkeypatch.setenv('OUTBOX_TOPIC_PREFIX', 'custom-service')
        monkeypatch.setenv('OUTBOX_TENANT_IDS', 't1, t2, t1, t3 ')

        settings = OutboxSettings.from_env()
        assert settings.poll_interval == 10.5
        assert settings.batch_size == 100
        assert settings.lease_seconds == 120.0
        assert settings.publish_timeout == 45.0
        assert settings.max_attempts == 10
        assert settings.retry_base == 5.0
        assert settings.retry_cap == 600.0
        assert settings.topic_prefix == 'custom-service'
        # Check stripping, ordering preserved, deduplicated
        assert settings.tenant_ids == ('t1', 't2', 't3')

    def test_from_env_empty_tenant_ids_raises(self, monkeypatch):
        # Empty string split(',') results in ('',) which fails regex validation
        monkeypatch.setenv('OUTBOX_TENANT_IDS', '')
        with pytest.raises(ValueError, match='Invalid outbox tenant allowlist'):
            OutboxSettings.from_env()
