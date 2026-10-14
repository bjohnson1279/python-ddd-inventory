import os
import re
from dataclasses import dataclass


def outbox_enabled():
    value = os.getenv('OUTBOX_ENABLED', 'false').lower()
    if value not in ('true', 'false'):
        raise ValueError('OUTBOX_ENABLED must be true or false')
    return value == 'true'


@dataclass(frozen=True)
class OutboxSettings:
    poll_interval: float = 2.0
    batch_size: int = 50
    lease_seconds: float = 60.0
    publish_timeout: float = 30.0
    max_attempts: int = 5
    retry_base: float = 2.0
    retry_cap: float = 300.0
    topic_prefix: str = 'inventory'
    tenant_ids: tuple[str, ...] | None = None

    def __post_init__(self):
        if not 0.01 <= self.poll_interval <= 300 or not 1 <= self.batch_size <= 1000:
            raise ValueError('Invalid outbox poll interval or batch size')
        if not 0 < self.publish_timeout <= 300 or not self.publish_timeout + 5 < self.lease_seconds <= 3600:
            raise ValueError('Outbox lease must exceed publish timeout by more than five seconds')
        if not 1 <= self.max_attempts <= 100 or not 0 < self.retry_base <= self.retry_cap <= 86400:
            raise ValueError('Invalid outbox retry settings')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', self.topic_prefix):
            raise ValueError('Invalid outbox Kafka topic prefix')
        if self.tenant_ids is not None and (not self.tenant_ids or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', t) for t in self.tenant_ids)):
            raise ValueError('Invalid outbox tenant allowlist')

    @classmethod
    def from_env(cls):
        allowed = os.getenv('OUTBOX_TENANT_IDS')
        return cls(poll_interval=float(os.getenv('OUTBOX_POLL_SECONDS', '2')),
            batch_size=int(os.getenv('OUTBOX_BATCH_SIZE', '50')),
            lease_seconds=float(os.getenv('OUTBOX_LEASE_SECONDS', '60')),
            publish_timeout=float(os.getenv('OUTBOX_PUBLISH_TIMEOUT_SECONDS', '30')),
            max_attempts=int(os.getenv('OUTBOX_MAX_ATTEMPTS', '5')),
            retry_base=float(os.getenv('OUTBOX_RETRY_BASE_SECONDS', '2')),
            retry_cap=float(os.getenv('OUTBOX_RETRY_CAP_SECONDS', '300')),
            topic_prefix=os.getenv('OUTBOX_TOPIC_PREFIX', 'inventory'),
            tenant_ids=tuple(dict.fromkeys(t.strip() for t in allowed.split(','))) if allowed is not None else None)
