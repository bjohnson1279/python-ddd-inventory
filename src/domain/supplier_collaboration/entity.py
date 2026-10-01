from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import uuid

@dataclass
class Supplier:
    tenant_id: str
    name: str
    contact_email: str
    status: str = 'ACTIVE'
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class PurchaseOrder:
    tenant_id: str
    supplier_id: str
    status: str = 'ISSUED' # ISSUED, ACKNOWLEDGED, SHIPPED, RECEIVED
    expected_delivery_date: Optional[datetime] = None
    acknowledged_date: Optional[datetime] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class ASNLineItem:
    sku: str
    shipped_quantity: int
    lot_number: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    asn_id: Optional[str] = None

@dataclass
class AdvanceShippingNotice:
    po_id: str
    supplier_id: str
    tracking_number: str
    estimated_delivery_date: datetime
    status: str = 'SUBMITTED' # SUBMITTED, RECEIVED, DISCREPANCY
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    items: List[ASNLineItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class SupplierPerformance:
    supplier_id: str
    otif_percentage: float = 100.0
    average_lead_time_variance_days: float = 0.0
    defect_rate_percentage: float = 0.0
    total_orders_evaluated: int = 0
