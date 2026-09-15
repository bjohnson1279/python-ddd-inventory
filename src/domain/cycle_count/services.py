from typing import List, Dict
from datetime import datetime
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord

class ABCClassificationService:
    def classify_sku(self, total_usage_value: float, total_org_value: float, thresholds: dict = None) -> str:
        if thresholds is None:
            thresholds = {'a_threshold': 0.90, 'b_threshold': 0.70}
        
        if total_org_value <= 0:
            return 'C'
            
        ratio = total_usage_value / total_org_value
        if ratio >= thresholds['a_threshold']:
            return 'A'
        if ratio >= thresholds['b_threshold']:
            return 'B'
        return 'C'

    def get_recommended_frequency(self, abc_class: str) -> int:
        mapping = {'A': 30, 'B': 90, 'C': 180}
        return mapping.get(abc_class, 180)

class CycleCountScheduler:
    def generate_audits(self, active_plans: List[CycleCountPlan], last_count_dates: Dict[str, datetime]) -> List[CycleCountRecord]:
        generated = []
        now = datetime.utcnow()
        
        for plan in active_plans:
            last_count = last_count_dates.get(plan.id)
            days_since = float('inf')
            if last_count:
                days_since = (now - last_count).days
                
            if days_since >= plan.frequency_days:
                generated.append(CycleCountRecord(
                    tenant_id=plan.tenant_id,
                    plan_id=plan.id,
                    name=f"Audit based on {plan.name}",
                    status='PENDING',
                    abc_classification=plan.abc_classification,
                    zone=plan.zone,
                    is_blind_count=True
                ))
        return generated
