from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

@dataclass
class Lot:
    id: str
    sku: str
    supplier_id: str
    expiration_date: datetime
    status: str = "ACTIVE" # ACTIVE, QUARANTINED, RECALLED
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class LotTrackingEngine:
    """Engine for FEFO, Quarantine, and Automated Recall pipelines."""

    def __init__(self):
        self._lots: List[Lot] = []

    def register_lot(self, lot: Lot):
        self._lots.append(lot)

    def enforce_fefo_allocation(self, sku: str, required_qty: int) -> List[Lot]:
        """First-Expired, First-Out allocation strategy."""
        # Filter for active lots of the SKU and sort by expiration date
        available_lots = [lot for lot in self._lots if lot.sku == sku and lot.status == "ACTIVE"]
        sorted_lots = sorted(available_lots, key=lambda l: l.expiration_date)
        
        # In a full system, Lot would have available quantities. For simplicity, we just return the ordered list.
        return sorted_lots

    def auto_quarantine_expired_lots(self):
        """Nightly cron job target to quarantine lots past their expiration date."""
        now = datetime.now(timezone.utc)
        quarantined_count = 0
        for lot in self._lots:
            if lot.status == "ACTIVE" and lot.expiration_date <= now:
                lot.status = "QUARANTINED"
                quarantined_count += 1
        return quarantined_count

    def trigger_supplier_recall(self, supplier_id: str, sku: str) -> List[Lot]:
        """Automated recall pipeline targeting specific supplier lots."""
        recalled_lots = []
        for lot in self._lots:
            if lot.supplier_id == supplier_id and lot.sku == sku:
                lot.status = "RECALLED"
                recalled_lots.append(lot)
        return recalled_lots
