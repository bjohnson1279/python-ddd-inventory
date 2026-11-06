from dataclasses import dataclass
from typing import Union
from src.domain.product.value_objects import SKU
from .value_objects import AgingBucket, RecommendedAction, AgingSKU

@dataclass
class DeadStockAnalysis:
    sku: Union[AgingSKU, SKU, str]
    location_id: str
    tenant_id: str
    current_quantity: int
    days_since_last_movement: int
    is_dead_stock: bool
    aging_bucket: AgingBucket
    locked_capital_cents: int
    recommended_action: RecommendedAction

    def __post_init__(self):
        if not isinstance(self.sku, AgingSKU):
            if isinstance(self.sku, SKU):
                self.sku = AgingSKU(self.sku.value)
            elif isinstance(self.sku, str):
                self.sku = AgingSKU(self.sku)
