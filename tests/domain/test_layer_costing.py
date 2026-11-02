from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from src.domain.product.layer_costing import consume, redistribute_value
from src.domain.product.costing import FIFOCosting, LIFOCosting, WACCosting, InventoryBatch

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def layer(name, quantity, cents, received=0, expires=None, quarantined=False, lot_number=None):
    return SimpleNamespace(id=name, remaining_quantity=quantity, unit_cost_cents=cents,
        remaining_value_cents=quantity * cents if cents is not None else None,
        received_at=NOW + timedelta(days=received), expiration_date=expires,
        lot_number=lot_number if lot_number is not None else name, quarantined=quarantined)


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


def test_validate_total_value_boundaries():
    from src.domain.product.layer_costing import validate_total_value, MAX_SAFE_CENTS
    validate_total_value(0)
    validate_total_value(MAX_SAFE_CENTS)
    with pytest.raises(ValueError, match="exceeds the supported integer-cent limit"):
        validate_total_value(-1)
    with pytest.raises(ValueError, match="exceeds the supported integer-cent limit"):
        validate_total_value(MAX_SAFE_CENTS + 1)


def test_eligible_quantity_filtering_and_lot_matching():
    from src.domain.product.layer_costing import eligible_quantity
    layers = [
        layer('active-1', 10, 100, received=-5),
        layer('active-2', 5, 200, received=-2, lot_number='LOT-A'),
        layer('expired', 8, 150, received=-10, expires=NOW - timedelta(days=1)),
        layer('quarantined', 7, 120, received=-1, quarantined=True),
        layer('depleted', 0, 100, received=-3),
    ]
    assert eligible_quantity(layers, 'FIFO', now=NOW) == 15
    assert eligible_quantity(layers, 'FIFO', now=NOW, lot_number='LOT-A') == 5
    assert eligible_quantity(layers, 'FIFO', now=NOW, lot_number='NONEXISTENT') == 0


def test_redistribute_value_zero_quantity_and_ties():
    empty_layers = [layer('e1', 0, 0), layer('e2', 0, 0)]
    redistribute_value(empty_layers, 0)
    assert [ly.remaining_value_cents for ly in empty_layers] == [0, 0]
    with pytest.raises(ValueError, match="Cannot assign value to empty inventory"):
        redistribute_value(empty_layers, 100)

    # Test tie-breaking by layer ID when remainders are identical
    tie_layers = [layer('b_layer', 1, 0), layer('a_layer', 1, 0)]
    redistribute_value(tie_layers, 1)
    assert tie_layers[0].remaining_value_cents == 0
    assert tie_layers[1].remaining_value_cents == 1


def test_consume_invalid_quantity_types_and_values():
    layers = [layer('a', 10, 100)]
    for invalid_qty in (0, -5, 2.5, True, False, "10"):
        with pytest.raises(ValueError, match="Quantity must be a positive integer"):
            consume(layers, invalid_qty, 'FIFO', now=NOW)


def test_consume_with_include_ineligible_and_layer_ids():
    layers = [
        layer('active', 5, 100, received=-10),
        layer('quarantined', 5, 200, received=-5, quarantined=True),
        layer('expired', 5, 300, received=-1, expires=NOW - timedelta(days=1)),
    ]
    # Normal consumption cannot pick up quarantined/expired
    with pytest.raises(ValueError, match="Insufficient eligible cost layers"):
        consume(layers, 10, 'FIFO', now=NOW)

    # With include_ineligible=True, should consume active and then quarantined
    cost, taken = consume(layers, 10, 'FIFO', now=NOW, include_ineligible=True)
    assert len(taken) == 2
    assert [t['layerId'] for t in taken] == ['active', 'quarantined']
    assert sum(t['quantity'] for t in taken) == 10
    assert cost == 1500

    # With specific layer_ids restriction
    layers_subset = [layer('l1', 5, 100), layer('l2', 5, 200)]
    cost_sub, taken_sub = consume(layers_subset, 3, 'FIFO', now=NOW, layer_ids=['l2'])
    assert len(taken_sub) == 1
    assert taken_sub[0]['layerId'] == 'l2'
    assert cost_sub == 600


def test_consume_fifo_with_initial_exact_value_and_unpriced():
    l_exact = layer('exact', 10, 100)
    l_exact.initial_quantity = 10
    l_exact.initial_value_cents = 1000
    cost, taken = consume([l_exact], 4, 'FIFO', now=NOW)
    assert cost == 400
    assert l_exact.remaining_quantity == 6
    assert l_exact.remaining_value_cents == 600

    # Test unpriced layer fully depleted
    l_unpriced = layer('unpriced', 2, None)
    cost_unpriced, taken_unpriced = consume([l_unpriced], 2, 'FIFO', now=NOW)
    assert cost_unpriced is None
    assert l_unpriced.remaining_quantity == 0
    assert l_unpriced.remaining_value_cents == 0
