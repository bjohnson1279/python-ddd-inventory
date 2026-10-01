from dataclasses import dataclass
from enum import Enum
from datetime import datetime

class TierName(Enum):
    FREE = "FREE"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"

class BillingEventType(Enum):
    API_OVERAGE = "API_OVERAGE"
    STORAGE_OVERAGE = "STORAGE_OVERAGE"
    NEW_SKU_TIER = "NEW_SKU_TIER"

@dataclass
class TenantBillingTier:
    tier_name: TierName
    max_requests_per_minute: int
    max_active_skus: int
    included_api_requests_per_month: int
    base_monthly_price_cents: int

@dataclass
class ApiUsageRecord:
    tenant_id: str
    billing_cycle_id: str
    api_requests_count: int
    storage_bytes_used: int
    active_skus_count: int

@dataclass
class BillingEvent:
    event_id: str
    tenant_id: str
    event_type: BillingEventType
    quantity: int
    occurred_at: datetime
