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
