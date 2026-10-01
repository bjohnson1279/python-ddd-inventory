from dataclasses import dataclass, field
from datetime import datetime
import uuid
from typing import Optional

@dataclass
class CycleCountPlan:
    tenant_id: str
    name: str
    abc_classification: str
    frequency_days: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    zone: Optional[str] = None
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class CycleCountRecord:
    tenant_id: str
    name: str
    status: str
    abc_classification: str
    is_blind_count: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: Optional[str] = None
    zone: Optional[str] = None
    assigned_operator_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class CycleCountLineItem:
    record_id: str
    sku: str
    expected_quantity: int
    counted_quantity: Optional[int] = None
    variance_quantity: Optional[int] = None
    variance_value: Optional[float] = None
    status: str = 'PENDING' # PENDING, MATCHED, VARIANCE_FLAGGED, RECOUNTED
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
