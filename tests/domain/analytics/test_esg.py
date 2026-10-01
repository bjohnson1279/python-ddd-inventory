import pytest
from src.domain.analytics.esg import ESGEmissionsTracker

class TestESGEmissionsTracker:
    def setup_method(self):
        self.tracker = ESGEmissionsTracker()

    def test_calculate_scope_3_transport_emissions_air(self):
        # 1000 km, 2000 kg, AIR
        # Expected: 1000 * (2000 / 1000) * 1.09 = 2180.0
        result = self.tracker.calculate_scope_3_transport_emissions(1000.0, 2000.0, "AIR")
        assert result == 2180.0

    def test_calculate_scope_3_transport_emissions_road(self):
        # 500 km, 1500 kg, ROAD
        # Expected: 500 * (1500 / 1000) * 0.10 = 75.0
        result = self.tracker.calculate_scope_3_transport_emissions(500.0, 1500.0, "ROAD")
        assert result == 75.0

    def test_calculate_scope_3_transport_emissions_sea(self):
        # 10000 km, 50000 kg, SEA
        # Expected: 10000 * (50000 / 1000) * 0.01 = 5000.0
        result = self.tracker.calculate_scope_3_transport_emissions(10000.0, 50000.0, "SEA")
        assert result == 5000.0

    def test_calculate_scope_3_transport_emissions_unknown_mode(self):
        # Should fallback to ROAD factor (0.10)
        # 500 km, 1500 kg, UNKNOWN
        # Expected: 500 * (1500 / 1000) * 0.10 = 75.0
        result = self.tracker.calculate_scope_3_transport_emissions(500.0, 1500.0, "UNKNOWN")
        assert result == 75.0

    def test_calculate_scope_3_transport_emissions_zero_distance(self):
        result = self.tracker.calculate_scope_3_transport_emissions(0.0, 1000.0, "ROAD")
        assert result == 0.0

    def test_calculate_scope_3_transport_emissions_zero_weight(self):
        result = self.tracker.calculate_scope_3_transport_emissions(100.0, 0.0, "ROAD")
        assert result == 0.0

    def test_calculate_scope_3_transport_emissions_negative_values(self):
        # Just to verify behavior, although might not make sense in reality
        result = self.tracker.calculate_scope_3_transport_emissions(-100.0, 1000.0, "ROAD")
        assert result == -10.0
