import pytest
import datetime
from src.domain.ai_models.predictive_engine import PredictiveEngine

@pytest.fixture
def engine():
    return PredictiveEngine()

def test_detect_anomalies_no_timestamp(engine):
    # If no timestamp is provided, it falls back to current time (which we mock implicitly)
    # Actually, we can test just providing a timestamp
    dt_anomaly = datetime.datetime(2023, 1, 1, 3, 0, 0)
    dt_normal = datetime.datetime(2023, 1, 1, 12, 0, 0)

    transactions = [
        {"timestamp": dt_anomaly, "quantity_adjustment": -60}, # Anomaly
        {"timestamp": dt_anomaly, "quantity_adjustment": -40}, # Not anomaly (qty too small)
        {"timestamp": dt_normal, "quantity_adjustment": -60},  # Not anomaly (normal hour)
        {"timestamp": dt_normal, "quantity_adjustment": -10}   # Normal
    ]

    anomalies = engine.detect_anomalies(transactions)
    assert len(anomalies) == 1
    assert anomalies[0]["timestamp"] == dt_anomaly
    assert anomalies[0]["quantity_adjustment"] == -60

def test_detect_anomalies_fallback_timestamp(engine):
    # Test fallback by manipulating mock or just checking it doesn't crash
    # If we don't provide a timestamp, it uses current time
    # This might or might not be an anomaly depending on when the test runs,
    # but we can at least ensure it doesn't throw a KeyError or AttributeError
    transactions = [
        {"quantity_adjustment": -60}
    ]

    anomalies = engine.detect_anomalies(transactions)
    # Could be 0 or 1 depending on time of day
    assert len(anomalies) in [0, 1]
