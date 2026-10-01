import pytest
from datetime import datetime, timedelta
from src.application.analytics.calculate_sales_velocity import CalculateSalesVelocity

@pytest.fixture
def use_case():
    return CalculateSalesVelocity()

@pytest.mark.asyncio
async def test_calculate_sales_velocity(use_case):
    now = datetime.utcnow()
    
    # Mock history:
    # 7 days ago: 10
    # 20 days ago: 20
    # 40 days ago: 30
    
    history = [
        {"sku": "TEST-1", "quantity": 10, "dispatched_at": now - timedelta(days=2)},
        {"sku": "TEST-1", "quantity": 20, "dispatched_at": now - timedelta(days=20)},
        {"sku": "TEST-1", "quantity": 30, "dispatched_at": now - timedelta(days=40)},
    ]
    
    # 7 day sum = 10 -> ADS = 1.429
    # 30 day sum = 30 -> ADS = 1.000
    # 90 day sum = 60 -> ADS = 0.667
    
    result = await use_case.execute(
        sku="TEST-1",
        location_id="default",
        current_stock=100,
        history=history
    )
    
    assert result["average_daily_sales_7d"] == 1.429
    assert result["average_daily_sales_30d"] == 1.0
    assert result["average_daily_sales_90d"] == 0.667
    assert result["days_of_cover"] == 100  # 100 / 1.0
    assert result["run_out_date"] is not None

@pytest.mark.asyncio
async def test_calculate_sales_velocity_zero_sales(use_case):
    result = await use_case.execute(
        sku="TEST-2",
        location_id="default",
        current_stock=50,
        history=[]
    )
    
    assert result["average_daily_sales_30d"] == 0.0
    assert result["days_of_cover"] == float('inf')
    assert result["run_out_date"] is None
