from dataclasses import dataclass
from enum import Enum

class AgingBucket(Enum):
    DAYS_0_30 = "0_30_DAYS"
    DAYS_31_60 = "31_60_DAYS"
    DAYS_61_90 = "61_90_DAYS"
    DAYS_91_180 = "91_180_DAYS"
    OVER_180_DAYS = "OVER_180_DAYS"

class RecommendedAction(Enum):
    NONE = "NONE"
    MARKDOWN = "MARKDOWN"
    LIQUIDATE = "LIQUIDATE"
    DONATE = "DONATE"
    SCRAP = "SCRAP"

@dataclass
class DeadStockAnalysis:
    sku: str
    location_id: str
    tenant_id: str
    current_quantity: int
    days_since_last_movement: int
    is_dead_stock: bool
    aging_bucket: AgingBucket
    locked_capital_cents: int
    recommended_action: RecommendedAction
