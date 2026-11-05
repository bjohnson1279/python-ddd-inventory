from dataclasses import dataclass
from enum import Enum
from typing import Union
from src.domain.product.value_objects import SKU

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
    sku: Union[SKU, str]
    location_id: str
    tenant_id: str
    current_quantity: int
    days_since_last_movement: int
    is_dead_stock: bool
    aging_bucket: AgingBucket
    locked_capital_cents: int
    recommended_action: RecommendedAction

    def __post_init__(self) -> None:
        if not isinstance(self.sku, SKU):
            self.sku = SKU(self.sku)
