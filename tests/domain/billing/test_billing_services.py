import time
from datetime import datetime, timezone
from src.domain.billing.entity import TenantBillingTier, TierName, ApiUsageRecord, BillingEventType
from src.domain.billing.services import RateLimitingService, UsageMeteringService, BillingHookService


class TestRateLimitingService:
    def test_allow_request_success(self):
        service = RateLimitingService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)
        now = time.time()

        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=10.0, last_refill_time=now, current_time=now)

        assert allowed is True
        assert new_tokens == 9.0
        assert new_refill_time == now

    def test_allow_request_default_current_time(self):
        service = RateLimitingService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)

        before = time.time()
        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=5.0, last_refill_time=before - 10)
        after = time.time()

        assert allowed is True
        assert before <= new_refill_time <= after

    def test_allow_request_insufficient_tokens(self):
        service = RateLimitingService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)
        now = time.time()

        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=0.5, last_refill_time=now, current_time=now)

        assert allowed is False
        assert new_tokens == 0.5
        assert new_refill_time == now

    def test_allow_request_token_refill_and_capacity_cap(self):
        service = RateLimitingService()
        # 60 requests/min = 1 token/sec. Capacity = 60
        tier = TenantBillingTier(TierName.PRO, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=1000)
        last_refill = 1000.0

        # 10 seconds elapsed -> 10 tokens added to 0.0 tokens -> 10.0 tokens total. Deduct 1.0 -> 9.0 remaining
        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=0.0, last_refill_time=last_refill, current_time=last_refill + 10.0)

        assert allowed is True
        assert new_tokens == 9.0
        assert new_refill_time == 1010.0

        # Test capacity cap: 200 seconds elapsed (200 tokens added), capped at capacity=60 -> minus 1 token = 59 remaining
        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=10.0, last_refill_time=last_refill, current_time=last_refill + 200.0)

        assert allowed is True
        assert new_tokens == 59.0
        assert new_refill_time == 1200.0

    def test_allow_request_negative_elapsed_time(self):
        service = RateLimitingService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)
        now = 1000.0

        # current_time earlier than last_refill_time should treat elapsed time as 0
        allowed, new_tokens, new_refill_time = service.allow_request(tier, current_tokens=2.0, last_refill_time=now + 10.0, current_time=now)

        assert allowed is True
        assert new_tokens == 1.0
        assert new_refill_time == now


class TestUsageMeteringService:
    def test_increment_api_usage(self):
        service = UsageMeteringService()
        record = ApiUsageRecord("T1", "2026-10", api_requests_count=5, storage_bytes_used=100, active_skus_count=10)

        service.increment_api_usage(record)
        assert record.api_requests_count == 6

    def test_record_storage_usage(self):
        service = UsageMeteringService()
        record = ApiUsageRecord("T1", "2026-10", api_requests_count=0, storage_bytes_used=1000, active_skus_count=0)

        # Increase storage usage updates high-water mark
        service.record_storage_usage(record, 2000)
        assert record.storage_bytes_used == 2000

        # Lowering storage usage does not decrease high-water mark
        service.record_storage_usage(record, 1500)
        assert record.storage_bytes_used == 2000

    def test_update_active_skus(self):
        service = UsageMeteringService()
        record = ApiUsageRecord("T1", "2026-10", api_requests_count=0, storage_bytes_used=0, active_skus_count=10)

        service.update_active_skus(record, 25)
        assert record.active_skus_count == 25


class TestBillingHookService:
    def test_evaluate_overages_no_overages(self):
        service = BillingHookService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)
        record = ApiUsageRecord("tenant-123", "2026-10", api_requests_count=500, storage_bytes_used=1024, active_skus_count=50)

        events = service.evaluate_overages(record, tier)
        assert events == []

    def test_evaluate_overages_api_and_sku(self):
        service = BillingHookService()
        tier = TenantBillingTier(TierName.PRO, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=5000)
        record = ApiUsageRecord("tenant-456", "2026-10", api_requests_count=1200, storage_bytes_used=1024, active_skus_count=150)
        fixed_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

        events = service.evaluate_overages(record, tier, current_time=fixed_time)

        assert len(events) == 2

        api_event = next(e for e in events if e.event_type == BillingEventType.API_OVERAGE)
        assert api_event.tenant_id == "tenant-456"
        assert api_event.quantity == 200
        assert api_event.occurred_at == fixed_time
        assert api_event.event_id is not None

        sku_event = next(e for e in events if e.event_type == BillingEventType.NEW_SKU_TIER)
        assert sku_event.tenant_id == "tenant-456"
        assert sku_event.quantity == 50
        assert sku_event.occurred_at == fixed_time
        assert sku_event.event_id is not None

    def test_evaluate_overages_default_current_time(self):
        service = BillingHookService()
        tier = TenantBillingTier(TierName.FREE, max_requests_per_minute=60, max_active_skus=100, included_api_requests_per_month=1000, base_monthly_price_cents=0)
        record = ApiUsageRecord("tenant-789", "2026-10", api_requests_count=1500, storage_bytes_used=1024, active_skus_count=50)

        before = datetime.now(timezone.utc)
        events = service.evaluate_overages(record, tier)
        after = datetime.now(timezone.utc)

        assert len(events) == 1
        assert events[0].event_type == BillingEventType.API_OVERAGE
        assert before <= events[0].occurred_at <= after
