from dataclasses import dataclass
from enum import Enum
from typing import Optional

class MarkdownStatus(Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    PUSHED_TO_CHANNELS = "PUSHED_TO_CHANNELS"

@dataclass
class LiquidationProfile:
    sku: str
    base_price_cents: int
    holding_cost_per_day_cents: int
    min_floor_price_cents: int

@dataclass
class InventoryYieldMetrics:
    sku: str
    days_in_inventory: int
    days_until_expiration: Optional[int]
    historical_daily_demand: float
    current_stock_quantity: int

@dataclass
class PriceMarkdownRecommendation:
    id: str
    sku: str
    recommended_price_cents: int
    reasoning: str
    status: MarkdownStatus = MarkdownStatus.PROPOSED
    
    def approve(self) -> None:
        if self.status != MarkdownStatus.PROPOSED:
            raise ValueError("Can only approve PROPOSED markdowns")
        self.status = MarkdownStatus.APPROVED
        
    def push_to_channels(self) -> None:
        if self.status != MarkdownStatus.APPROVED:
            raise ValueError("Can only push APPROVED markdowns")
        self.status = MarkdownStatus.PUSHED_TO_CHANNELS
