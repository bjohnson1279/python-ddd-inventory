from enum import Enum

class RMAStatus(str, Enum):
    OPEN = "OPEN"
    INSPECTION = "INSPECTION"
    REFUNDED = "REFUNDED"
    SCRAPPED = "SCRAPPED"
