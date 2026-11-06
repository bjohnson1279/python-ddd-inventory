from enum import Enum
from src.domain.product.value_objects import SKU

class AgingBucket(str, Enum):
    DAYS_0_30 = "0_30_DAYS"
    DAYS_31_60 = "31_60_DAYS"
    DAYS_61_90 = "61_90_DAYS"
    DAYS_91_180 = "91_180_DAYS"
    OVER_180_DAYS = "OVER_180_DAYS"

class RecommendedAction(str, Enum):
    NONE = "NONE"
    MARKDOWN = "MARKDOWN"
    LIQUIDATE = "LIQUIDATE"
    DONATE = "DONATE"
    SCRAP = "SCRAP"

class AgingSKU(SKU):
    def __eq__(self, other):
        if isinstance(other, str):
            return self.value == other
        if isinstance(other, SKU):
            return self.value == other.value
        return False

    def __hash__(self):
        return hash(self.value)
