import pytest
from decimal import Decimal
from src.domain.product.costing import InventoryBatch, FIFOCosting, LIFOCosting, WACCosting

@pytest.fixture
def sample_batches():
    return [
        InventoryBatch(batch_id="B1", quantity=10, unit_cost=Decimal('10.00'), received_at="2023-01-01T10:00:00Z"),
        InventoryBatch(batch_id="B2", quantity=20, unit_cost=Decimal('12.00'), received_at="2023-01-02T10:00:00Z"),
        InventoryBatch(batch_id="B3", quantity=15, unit_cost=Decimal('15.00'), received_at="2023-01-03T10:00:00Z"),
    ]

def test_fifo_costing(sample_batches):
    strategy = FIFOCosting()
    # 25 units sold. Should take 10 from B1 (10*10=100) and 15 from B2 (15*12=180). Total = 280
    cost = strategy.calculate_cost_of_goods_sold(25, sample_batches)
    assert cost == Decimal('280.00')

def test_lifo_costing(sample_batches):
    strategy = LIFOCosting()
    # 25 units sold. Should take 15 from B3 (15*15=225) and 10 from B2 (10*12=120). Total = 345
    cost = strategy.calculate_cost_of_goods_sold(25, sample_batches)
    assert cost == Decimal('345.00')

def test_wac_costing():
    strategy = WACCosting()
    wac_batches = [
        InventoryBatch(batch_id="W1", quantity=10, unit_cost=Decimal('10.00'), received_at="2023-01-01"),
        InventoryBatch(batch_id="W2", quantity=10, unit_cost=Decimal('20.00'), received_at="2023-01-02"),
    ]
    # Total qty: 20, Total value: 300, Avg: 15.00
    # Cost for 10 units = 10 * 15 = 150
    cost = strategy.calculate_cost_of_goods_sold(10, wac_batches)
    assert cost == Decimal('150.00')

def test_costing_not_enough_inventory(sample_batches):
    # Total is 45, request 50
    for strategy in [FIFOCosting(), LIFOCosting(), WACCosting()]:
        with pytest.raises(ValueError, match="Not enough inventory"):
            strategy.calculate_cost_of_goods_sold(50, sample_batches)

def test_costing_zero_quantity(sample_batches):
    for strategy in [FIFOCosting(), LIFOCosting(), WACCosting()]:
        cost = strategy.calculate_cost_of_goods_sold(0, sample_batches)
        assert cost == Decimal('0.00')

def test_costing_exact_quantity(sample_batches):
    # Total is 45, total value is 565
    for strategy in [FIFOCosting(), LIFOCosting(), WACCosting()]:
        cost = strategy.calculate_cost_of_goods_sold(45, sample_batches)
        assert round(cost, 2) == Decimal('565.00')

def test_wac_empty_or_zero_total_quantity():
    strategy = WACCosting()
    cost = strategy.calculate_cost_of_goods_sold(0, [])
    assert cost == Decimal('0.00')
