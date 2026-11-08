from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import List, Dict, Union
from src.domain.logistics.value_objects import RMAStatus

@dataclass
class RMACase:
    case_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str = ""
    customer_id: str = ""
    status: Union[RMAStatus, str] = RMAStatus.OPEN # OPEN, INSPECTION, REFUNDED, SCRAPPED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = RMAStatus(self.status)

class ReverseLogisticsWorkflow:
    def __init__(self):
        self.rma_cases: List[RMACase] = []
        # O(1) index for faster lookup
        self._rma_index: Dict[str, RMACase] = {}

    def create_rma(self, order_id: str, customer_id: str) -> RMACase:
        case = RMACase(order_id=order_id, customer_id=customer_id)
        self.rma_cases.append(case)
        self._rma_index[case.case_id] = case
        return case

    def update_inspection_result(self, case_id: str, result: Union[RMAStatus, str]):
        # Bolt Optimization: Convert O(N) list traversal into O(1) dictionary lookup
        # Impact: Dramatically reduces lookup time when updating RMA cases in a large list
        case = self._rma_index.get(case_id)
        if case:
            case.status = RMAStatus(result) if isinstance(result, str) else result
