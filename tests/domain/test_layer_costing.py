from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from src.domain.product.layer_costing import consume, redistribute_value
from src.domain.product.costing import FIFOCosting, LIFOCosting, WACCosting, InventoryBatch

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def layer(name, quantity, cents, received=0, expires=None, quarantined=False):
    return SimpleNamespace(id=name, remaining_quantity=quantity, unit_cost_cents=cents,
        remaining_value_cents=quantity * cents if cents is not None else None,
        received_at=NOW + timedelta(days=received), expiration_date=expires,
        lot_number=name, quarantined=quarantined)


@pytest.mark.parametrize('method,expected', [('FIFO', 1400), ('LIFO', 2200), ('WAC', 1800)])
def test_costing_vectors_and_remaining_value(method, expected):
    layers = [layer('a', 10, 100, -2), layer('b', 10, 200, -1)]
    if method == 'WAC':
        redistribute_value(layers, 3000)
    cost, taken = consume(layers, 12, method, now=NOW)
    assert cost == expected
    assert sum(item['quantity'] for item in taken) == 12
    assert sum(item.remaining_quantity for item in layers) == 8
    assert sum(item.remaining_value_cents for item in layers) == 3000 - expected


def test_fefo_excludes_expired_and_quarantined_lots_and_orders_null_last():
    layers = [layer('no-expiry', 1, 100, -20),
              layer('expired', 10, 100, expires=NOW),
              layer('quarantine', 10, 100, expires=NOW + timedelta(days=1), quarantined=True),
              layer('later', 1, 300, expires=NOW + timedelta(days=3)),
              layer('early', 1, 200, expires=NOW + timedelta(days=2))]
    cost, taken = consume(layers, 3, 'FEFO', now=NOW)
    assert [item['lotNumber'] for item in taken] == ['early', 'later', 'no-expiry']
    assert cost == 600
    assert layers[1].remaining_quantity == layers[2].remaining_quantity == 10


def test_insufficient_lot_fails_before_modifying_any_layer():
    layers = [layer('a', 5, 100), layer('b', 5, 200)]
    with pytest.raises(ValueError, match='Insufficient eligible'):
        consume(layers, 6, 'FIFO', now=NOW, lot_number='a')
    assert [item.remaining_quantity for item in layers] == [5, 5]


def test_wac_rounding_conserves_every_penny_through_depletion():
    layers = [layer('a', 1, 1), layer('b', 2, 2)]
    redistribute_value(layers, 5)
    costs = [consume(layers, 1, 'WAC', now=NOW)[0] for _ in range(3)]
    assert costs == [2, 2, 1]
    assert sum(costs) == 5
    assert sum(item.remaining_value_cents for item in layers) == 0


def test_unpriced_is_distinct_from_zero_and_blocks_average():
    layers = [layer('unknown', 1, None), layer('free', 1, 0, 1)]
    with pytest.raises(ValueError, match='Price unpriced'):
        consume(layers, 1, 'WAC', now=NOW)
    assert consume(layers, 1, 'FIFO', now=NOW)[0] is None
    assert consume(layers, 1, 'FIFO', now=NOW)[0] == 0


@pytest.mark.parametrize('quantity', [-1, 1.5, True])
@pytest.mark.parametrize('strategy', [FIFOCosting, LIFOCosting, WACCosting])
def test_legacy_costing_rejects_invalid_sale_quantity(strategy, quantity):
    with pytest.raises(ValueError):
        strategy().calculate_cost_of_goods_sold(quantity, [])


@pytest.mark.parametrize('cost', [Decimal('-1'), Decimal('NaN'), Decimal('Infinity')])
def test_legacy_costing_rejects_invalid_batch_cost(cost):
    for strategy in (FIFOCosting(), LIFOCosting(), WACCosting()):
        with pytest.raises(ValueError):
            strategy.calculate_cost_of_goods_sold(1, [InventoryBatch('bad', 1, cost, '2026-01-01')])
