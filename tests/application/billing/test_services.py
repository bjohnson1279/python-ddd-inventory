import pytest
import time
from datetime import datetime, timezone
from src.domain.billing.entity import TenantBillingTier, TierName, ApiUsageRecord, BillingEventType
from src.domain.billing.services import RateLimitingService, UsageMeteringService, BillingHookService

def test_rate_limiting_service():
    service = RateLimitingService()
    tier = TenantBillingTier(TierName.FREE, 60, 100, 1000, 0)
    
    # Start with 60 tokens
    now = time.time()
    allowed, new_tokens, new_time = service.allow_request(tier, 60.0, now, now)
    assert allowed is True
    assert new_tokens == 59.0
    
    # Try with 0 tokens and no time elapsed
    allowed, new_tokens, new_time = service.allow_request(tier, 0.0, now, now)
    assert allowed is False
    
    # Try with 0 tokens and 1 second elapsed (should add 1 token since rate is 60/60)
    allowed, new_tokens, new_time = service.allow_request(tier, 0.0, now, now + 1.0)
    assert allowed is True
    assert new_tokens == 0.0

def test_usage_metering_service():
    service = UsageMeteringService()
    record = ApiUsageRecord("T1", "2026-10", 0, 0, 0)
    
    service.increment_api_usage(record)
    assert record.api_requests_count == 1
    
    service.record_storage_usage(record, 1024)
    assert record.storage_bytes_used == 1024
    
    # Should not lower the high-water mark
    service.record_storage_usage(record, 512)
    assert record.storage_bytes_used == 1024

def test_billing_hook_service():
    service = BillingHookService()
    tier = TenantBillingTier(TierName.PRO, 60, 100, 1000, 0)
    
    record = ApiUsageRecord("T1", "2026-10", 1500, 0, 150)
    now = datetime.now(timezone.utc)
    
    events = service.evaluate_overages(record, tier, now)
    assert len(events) == 2
    
    api_event = next(e for e in events if e.event_type == BillingEventType.API_OVERAGE)
    assert api_event.quantity == 500
    
    sku_event = next(e for e in events if e.event_type == BillingEventType.NEW_SKU_TIER)
    assert sku_event.quantity == 50
