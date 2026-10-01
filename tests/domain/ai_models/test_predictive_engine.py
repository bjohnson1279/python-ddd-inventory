import datetime
from src.domain.ai_models.predictive_engine import PredictiveEngine

def test_detect_anomalies():
    engine = PredictiveEngine()

    # Setup test data
    time_anomaly = datetime.datetime(2024, 1, 1, 3, 0, 0)
    time_normal = datetime.datetime(2024, 1, 1, 12, 0, 0)

    transactions = [
        # Anomaly: large negative adjustment outside normal hours (3 AM)
        {"id": "t1", "timestamp": time_anomaly, "quantity_adjustment": -100},
        # Normal: large negative adjustment inside normal hours (12 PM)
        {"id": "t2", "timestamp": time_normal, "quantity_adjustment": -100},
        # Normal: small negative adjustment outside normal hours (3 AM)
        {"id": "t3", "timestamp": time_anomaly, "quantity_adjustment": -10},
        # Missing timestamp, relies on fallback which is datetime.now()
        # For tests, we mock or allow fallback logic to just execute without failing
        {"id": "t4", "quantity_adjustment": -10},
    ]

    anomalies = engine.detect_anomalies(transactions)

    assert len(anomalies) >= 1
    assert any(a["id"] == "t1" for a in anomalies)
    assert not any(a["id"] == "t2" for a in anomalies)
    assert not any(a["id"] == "t3" for a in anomalies)

def test_calculate_dynamic_rop_empty():
    engine = PredictiveEngine()
    rop = engine.calculate_dynamic_rop("SKU-1", [], 5)
    assert rop == 0.0

def test_calculate_dynamic_rop():
    engine = PredictiveEngine()
    historical_sales = [10, 20, 30] # avg = 20, max = 30
    lead_time_days = 2

    # expected safety stock = (30 * 2) - (20 * 2) = 60 - 40 = 20
    # expected rop = (20 * 2) + 20 = 40 + 20 = 60
    rop = engine.calculate_dynamic_rop("SKU-1", historical_sales, lead_time_days)
    assert rop == 60.0

def test_optimize_slotting():
    engine = PredictiveEngine()
    warehouse_map = {}
    sku_velocity = {
        "SKU-SLOW": 50,
        "SKU-FAST": 150,
    }
    recommendations = engine.optimize_slotting(warehouse_map, sku_velocity)

    assert len(recommendations) == 1
    assert recommendations[0]["sku"] == "SKU-FAST"
    assert recommendations[0]["recommended_zone"] == "A_Aisle_Forward_Pick"

def test_calculate_rebalancing_matrix():
    engine = PredictiveEngine()
    regional_demand = {"North": 100}
    warehouse_stock = {"North": 50}
    transfers = engine.calculate_rebalancing_matrix(regional_demand, warehouse_stock)

    assert isinstance(transfers, list)
    assert len(transfers) == 0
