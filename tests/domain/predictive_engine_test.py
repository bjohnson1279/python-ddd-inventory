import pytest
import datetime
from src.domain.ai_models.predictive_engine import PredictiveEngine

def test_detect_anomalies_short_circuit_logic():
    engine = PredictiveEngine()

    # Time outside normal hours (3 AM)
    abnormal_time = datetime.datetime(2023, 1, 1, 3, 0, 0)

    # Time inside normal hours (12 PM)
    normal_time = datetime.datetime(2023, 1, 1, 12, 0, 0)

    transactions = [
        {"timestamp": abnormal_time, "quantity_adjustment": -60}, # Anomaly
        {"timestamp": abnormal_time, "quantity_adjustment": -40}, # Not an anomaly (qty too high)
        {"timestamp": normal_time, "quantity_adjustment": -60},   # Not an anomaly (time normal)
        {"timestamp": normal_time, "quantity_adjustment": 10},    # Not an anomaly
        {"quantity_adjustment": -100}, # Missing timestamp but qty <-50. Hour depends on runtime `now()`. For robust testing, we can inject a mock or just test the short circuit
    ]

    # Let's mock datetime inside the module to control `default_now` behavior for the last item
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2023, 1, 1, 4, 0, 0)

    import src.domain.ai_models.predictive_engine
    original_datetime = src.domain.ai_models.predictive_engine.datetime.datetime
    src.domain.ai_models.predictive_engine.datetime.datetime = MockDatetime

    try:
        anomalies = engine.detect_anomalies(transactions)

        assert len(anomalies) == 2
        assert anomalies[0] == transactions[0]
        assert anomalies[1] == transactions[4] # because MockDatetime returns 4 AM
    finally:
        src.domain.ai_models.predictive_engine.datetime.datetime = original_datetime
