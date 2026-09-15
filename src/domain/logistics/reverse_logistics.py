from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import List

@dataclass
class RMACase:
    case_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str = ""
    customer_id: str = ""
    status: str = "OPEN" # OPEN, INSPECTION, REFUNDED, SCRAPPED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class ReverseLogisticsWorkflow:
    def __init__(self):
        self.rma_cases: List[RMACase] = []

    def create_rma(self, order_id: str, customer_id: str) -> RMACase:
        case = RMACase(order_id=order_id, customer_id=customer_id)
        self.rma_cases.append(case)
        return case

    def update_inspection_result(self, case_id: str, result: str):
        for case in self.rma_cases:
            if case.case_id == case_id:
                case.status = result
                break
