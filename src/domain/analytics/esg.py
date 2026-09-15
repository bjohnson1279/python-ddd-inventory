class ESGEmissionsTracker:
    def calculate_scope_3_transport_emissions(self, distance_km: float, weight_kg: float, mode: str) -> float:
        """Transport mode and warehouse energy carbon emissions calculator."""
        emission_factors = {
            "AIR": 1.09,
            "ROAD": 0.10,
            "SEA": 0.01
        }
        factor = emission_factors.get(mode, 0.10)
        return distance_km * (weight_kg / 1000) * factor
