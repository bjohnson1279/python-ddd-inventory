from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Union
from src.domain.product.value_objects import SKU, LotStatus

@dataclass
class Lot:
    id: str
    sku: Union[SKU, str]
    supplier_id: str
    expiration_date: datetime
    status: Union[LotStatus, str] = LotStatus.ACTIVE # ACTIVE, QUARANTINED, RECALLED
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.sku, SKU):
            self.sku = SKU(self.sku)
        if isinstance(self.status, str):
            self.status = LotStatus(self.status)

class LotTrackingEngine:
    """Engine for FEFO, Quarantine, and Automated Recall pipelines."""

    def __init__(self):
        self._lots: List[Lot] = []

    def register_lot(self, lot: Lot):
        self._lots.append(lot)

    def enforce_fefo_allocation(self, sku: Union[SKU, str], required_qty: int) -> List[Lot]:
        """First-Expired, First-Out allocation strategy."""
        sku_obj = sku if isinstance(sku, SKU) else SKU(sku)
        # Filter for active lots of the SKU and sort by expiration date
        available_lots = [lot for lot in self._lots if lot.sku == sku_obj and lot.status == LotStatus.ACTIVE]
        sorted_lots = sorted(available_lots, key=lambda l: l.expiration_date)
        
        # In a full system, Lot would have available quantities. For simplicity, we just return the ordered list.
        return sorted_lots

    def auto_quarantine_expired_lots(self):
        """Nightly cron job target to quarantine lots past their expiration date."""
        now = datetime.now(timezone.utc)
        quarantined_count = 0
        for lot in self._lots:
            if lot.status == LotStatus.ACTIVE and lot.expiration_date <= now:
                lot.status = LotStatus.QUARANTINED
                quarantined_count += 1
        return quarantined_count

    def trigger_supplier_recall(self, supplier_id: str, sku: Union[SKU, str]) -> List[Lot]:
        """Automated recall pipeline targeting specific supplier lots."""
        sku_obj = sku if isinstance(sku, SKU) else SKU(sku)
        recalled_lots = []
        for lot in self._lots:
            if lot.supplier_id == supplier_id and lot.sku == sku_obj:
                lot.status = LotStatus.RECALLED
                recalled_lots.append(lot)
        return recalled_lots
