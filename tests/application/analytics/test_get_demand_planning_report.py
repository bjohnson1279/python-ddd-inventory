import pytest
from unittest.mock import AsyncMock, MagicMock
from src.application.analytics.get_demand_planning_report import GetDemandPlanningReport
from src.application.analytics.calculate_sales_velocity import CalculateSalesVelocity
from src.domain.analytics.repository import AnalyticsRepository

@pytest.fixture
def repo():
    mock = AsyncMock(spec=AnalyticsRepository)
    return mock

@pytest.fixture
def velocity_calc():
    mock = AsyncMock(spec=CalculateSalesVelocity)
    return mock

@pytest.fixture
def use_case(repo, velocity_calc):
    return GetDemandPlanningReport(repo, velocity_calc)

@pytest.mark.asyncio
async def test_get_demand_planning_report(use_case, repo, velocity_calc):
    repo.get_inventory_levels_by_location.return_value = [
        {"sku": "ITEM-A", "quantity": 5}
    ]
    repo.get_reorder_policies.return_value = [
        {"sku": "ITEM-A", "reorder_point": 10, "reorder_quantity": 50, "safety_stock": 5}
    ]
    repo.get_active_forecasts.return_value = []
    repo.fetch_dispatch_history.return_value = []
    
    velocity_calc.execute.return_value = {
        "average_daily_sales_7d": 1,
        "average_daily_sales_30d": 2,
        "average_daily_sales_90d": 1.5,
        "days_of_cover": 2.5,
        "run_out_date": None
    }
    
    report = await use_case.execute("loc-1")
    
    assert len(report) == 1
    item = report[0]
    
    assert item["sku"] == "ITEM-A"
    assert item["current_stock"] == 5
    assert item["reorder_point"] == 10
    
    # 5 <= 10, so action is required
    assert item["action_required"] is True
    assert item["recommended_order_quantity"] == 50
    
    # Forecast default fallback is Math.ceil(ads_30d * 30) = ceil(2 * 30) = 60
    assert item["forecasted_demand_30d"] == 60
    assert item["confidence_level"] == 0.70
