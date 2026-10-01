import time
import uuid
from typing import Tuple, List
from datetime import datetime, timezone
from .entity import TenantBillingTier, ApiUsageRecord, BillingEvent, BillingEventType

class RateLimitingService:
    def allow_request(
        self,
        tier: TenantBillingTier,
        current_tokens: float,
        last_refill_time: float,
        current_time: float = None
    ) -> Tuple[bool, float, float]:
        """
        Token Bucket algorithm.
        Returns (is_allowed, new_tokens, new_last_refill_time)
        """
        if current_time is None:
            current_time = time.time()
            
        capacity = tier.max_requests_per_minute
        # Refill rate per second
        refill_rate = capacity / 60.0
        
        elapsed = max(0, current_time - last_refill_time)
        tokens_to_add = elapsed * refill_rate
        
        new_tokens = min(capacity, current_tokens + tokens_to_add)
        
        if new_tokens >= 1.0:
            return True, new_tokens - 1.0, current_time
        else:
            # We don't advance the refill time if we didn't consume a token
            return False, new_tokens, current_time

class UsageMeteringService:
    def increment_api_usage(self, record: ApiUsageRecord) -> None:
        record.api_requests_count += 1
        
    def record_storage_usage(self, record: ApiUsageRecord, bytes_used: int) -> None:
        if bytes_used > record.storage_bytes_used:
            record.storage_bytes_used = bytes_used
            
    def update_active_skus(self, record: ApiUsageRecord, sku_count: int) -> None:
        record.active_skus_count = sku_count

class BillingHookService:
    def evaluate_overages(
        self,
        record: ApiUsageRecord,
        tier: TenantBillingTier,
        current_time: datetime = None
    ) -> List[BillingEvent]:
        
        if current_time is None:
            current_time = datetime.now(timezone.utc)
            
        events = []
        
        # Check API Overages
        if record.api_requests_count > tier.included_api_requests_per_month:
            overage = record.api_requests_count - tier.included_api_requests_per_month
            # Emit event for the overage block (e.g. per 1 request for simplicity, but usually batched)
            events.append(BillingEvent(
                event_id=str(uuid.uuid4()),
                tenant_id=record.tenant_id,
                event_type=BillingEventType.API_OVERAGE,
                quantity=overage,
                occurred_at=current_time
            ))
            # Reset count or rely on idempotent external processing logic
            
        # Check SKU tier overage
        if record.active_skus_count > tier.max_active_skus:
            overage = record.active_skus_count - tier.max_active_skus
            events.append(BillingEvent(
                event_id=str(uuid.uuid4()),
                tenant_id=record.tenant_id,
                event_type=BillingEventType.NEW_SKU_TIER,
                quantity=overage,
                occurred_at=current_time
            ))
            
        return events
