from dataclasses import dataclass
from typing import List, Dict, Optional
import math

@dataclass
class Location:
    id: str
    lat: float
    lon: float

@dataclass
class OrderLine:
    sku: str
    quantity: int

@dataclass
class StockLevel:
    location_id: str
    sku: str
    available_qty: int

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in km between two points on the earth."""
    R = 6371  # Radius of the earth in km
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat / 2) * math.sin(dLat / 2) +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dLon / 2) * math.sin(dLon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class OrderRoutingEngine:
    """Intelligent Multi-Location Order Routing Engine."""

    def __init__(self, locations: List[Location], stock_levels: List[StockLevel]):
        self.locations = {loc.id: loc for loc in locations}
        self.stock_levels = stock_levels
        self.stock_lookup = {(stock.location_id, stock.sku): stock for stock in stock_levels}

    def _get_available_qty(self, location_id: str, sku: str) -> int:
        stock = self.stock_lookup.get((location_id, sku))
        return stock.available_qty if stock else 0

    def route_order(self, customer_lat: float, customer_lon: float, order_lines: List[OrderLine]) -> Dict[str, List[OrderLine]]:
        """
        Optimizes splits and location distance. Returns a dictionary mapping
        location_id to the order lines it should fulfill.
        """
        routing_plan = {}
        
        # Sort locations by distance to customer
        sorted_locations = sorted(
            self.locations.values(),
            key=lambda loc: haversine_distance(customer_lat, customer_lon, loc.lat, loc.lon)
        )

        for line in order_lines:
            remaining_qty = line.quantity
            
            for loc in sorted_locations:
                if remaining_qty <= 0:
                    break
                    
                available = self._get_available_qty(loc.id, line.sku)
                if available > 0:
                    allocate = min(available, remaining_qty)
                    
                    if loc.id not in routing_plan:
                        routing_plan[loc.id] = []
                    
                    routing_plan[loc.id].append(OrderLine(sku=line.sku, quantity=allocate))
                    
                    # Deduct from internal stock levels
                    stock = self.stock_lookup.get((loc.id, line.sku))
                    if stock:
                        stock.available_qty -= allocate
                            
                    remaining_qty -= allocate
                    
            if remaining_qty > 0:
                # In a real system, this would trigger backorders or drop-shipping workflows
                raise ValueError(f"Insufficient stock across all locations to fulfill SKU {line.sku}")

        return routing_plan
