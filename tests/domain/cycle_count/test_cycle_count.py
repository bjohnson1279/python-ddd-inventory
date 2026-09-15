from src.domain.cycle_count.services import ABCClassificationService, CycleCountScheduler
from src.domain.cycle_count.entity import CycleCountPlan
import pytest

def test_abc_classification():
    service = ABCClassificationService()
    
    # 95% of total value -> A class
    assert service.classify_sku(950, 1000) == 'A'
    
    # 80% -> B class
    assert service.classify_sku(800, 1000) == 'B'
    
    # 50% -> C class
    assert service.classify_sku(500, 1000) == 'C'

def test_frequency():
    service = ABCClassificationService()
    assert service.get_recommended_frequency('A') == 30
    assert service.get_recommended_frequency('B') == 90
    assert service.get_recommended_frequency('C') == 180

def test_scheduler():
    scheduler = CycleCountScheduler()
    plan = CycleCountPlan(
        tenant_id="tenant-1",
        name="Daily A-Items",
        abc_classification="A",
        frequency_days=30
    )
    # With no last counts, it should generate an audit immediately
    audits = scheduler.generate_audits([plan], {})
    assert len(audits) == 1
    assert audits[0].abc_classification == "A"
