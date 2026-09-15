import pytest
from src.domain.routing import (
    Location,
    OrderLine,
    StockLevel,
    haversine_distance,
    OrderRoutingEngine,
)

def test_haversine_distance():
    # Distance from a point to itself should be 0
    assert haversine_distance(40.7128, -74.0060, 40.7128, -74.0060) == 0.0

    # Distance between New York and London should be approx 5570 km
    dist = haversine_distance(40.7128, -74.0060, 51.5074, -0.1278)
    assert 5500 < dist < 5600

def test_routing_engine_get_available_qty():
    locations = [Location("loc1", 0.0, 0.0)]
    stock_levels = [
        StockLevel("loc1", "SKU1", 10),
        StockLevel("loc1", "SKU2", 0),
    ]
    engine = OrderRoutingEngine(locations, stock_levels)

    assert engine._get_available_qty("loc1", "SKU1") == 10
    assert engine._get_available_qty("loc1", "SKU2") == 0
    assert engine._get_available_qty("loc1", "SKU3") == 0
    assert engine._get_available_qty("loc2", "SKU1") == 0

def test_route_order_closest_location():
    # Customer is at (0, 0)
    customer_lat, customer_lon = 0.0, 0.0

    loc1 = Location("loc1", 1.0, 1.0) # Farther
    loc2 = Location("loc2", 0.1, 0.1) # Closer

    locations = [loc1, loc2]
    stock_levels = [
        StockLevel("loc1", "SKU1", 100),
        StockLevel("loc2", "SKU1", 100),
    ]

    engine = OrderRoutingEngine(locations, stock_levels)
    order_lines = [OrderLine("SKU1", 5)]

    plan = engine.route_order(customer_lat, customer_lon, order_lines)

    # Should be entirely fulfilled by loc2
    assert "loc2" in plan
    assert "loc1" not in plan
    assert len(plan["loc2"]) == 1
    assert plan["loc2"][0].sku == "SKU1"
    assert plan["loc2"][0].quantity == 5

    # Check internal stock was decremented
    assert engine._get_available_qty("loc2", "SKU1") == 95

def test_route_order_split_fulfillment():
    customer_lat, customer_lon = 0.0, 0.0

    loc1 = Location("loc1", 1.0, 1.0) # Farther
    loc2 = Location("loc2", 0.1, 0.1) # Closer

    locations = [loc1, loc2]
    stock_levels = [
        StockLevel("loc1", "SKU1", 10),
        StockLevel("loc2", "SKU1", 3), # Closer location only has 3
    ]

    engine = OrderRoutingEngine(locations, stock_levels)
    order_lines = [OrderLine("SKU1", 8)]

    plan = engine.route_order(customer_lat, customer_lon, order_lines)

    # Should be fulfilled by both
    assert "loc2" in plan
    assert "loc1" in plan

    assert len(plan["loc2"]) == 1
    assert plan["loc2"][0].sku == "SKU1"
    assert plan["loc2"][0].quantity == 3 # Took all 3 from closer loc

    assert len(plan["loc1"]) == 1
    assert plan["loc1"][0].sku == "SKU1"
    assert plan["loc1"][0].quantity == 5 # Took remaining 5 from farther loc

    # Check internal stock was decremented
    assert engine._get_available_qty("loc2", "SKU1") == 0
    assert engine._get_available_qty("loc1", "SKU1") == 5

def test_route_order_insufficient_stock():
    customer_lat, customer_lon = 0.0, 0.0

    loc1 = Location("loc1", 1.0, 1.0)
    locations = [loc1]
    stock_levels = [StockLevel("loc1", "SKU1", 5)]

    engine = OrderRoutingEngine(locations, stock_levels)
    order_lines = [OrderLine("SKU1", 10)] # Ordering more than available

    with pytest.raises(ValueError) as excinfo:
        engine.route_order(customer_lat, customer_lon, order_lines)

    assert "Insufficient stock across all locations to fulfill SKU SKU1" in str(excinfo.value)
