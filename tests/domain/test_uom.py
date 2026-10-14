from types import SimpleNamespace
from fractions import Fraction
import pytest
from src.domain.uom import base_quantity, factor, cost_groups, receipt_value
from datetime import datetime, timezone
from src.domain.product.layer_costing import consume


def test_case_and_measured_conversion_are_exact_and_conserve_money():
    discrete = SimpleNamespace(category='discrete', base_unit='ea', rules=[{'unit': 'case', 'factorToBase': '24'}])
    assert base_quantity(discrete, '0.5', 'case') == 12
    assert factor(discrete, 'case') == Fraction(24)
    measured = SimpleNamespace(category='weight', base_unit='g', rules=[])
    assert base_quantity(measured, '1.5', 'kg') == 1500
    assert receipt_value('1.5', 199) == 299
    groups = cost_groups(48, 2002)
    assert sum(q for q, c in groups) == 48
    assert groups == [(48, 42)]  # Rounded display cost; the layer carries the exact 2002 cents.


@pytest.mark.parametrize('amount,unit', [('0', 'case'), ('0.01', 'case'), (1.5, 'case'), (True, 'case'), ('1e3', 'case'), ('1', 'kg'), ('999999999', 'case')])
def test_invalid_or_inexact_ledger_quantities_are_rejected(amount, unit):
    config = SimpleNamespace(category='discrete', base_unit='ea', rules=[{'unit': 'case', 'factorToBase': '24'}])
    with pytest.raises(ValueError):
        base_quantity(config, amount, unit)


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'FEFO', 'WAC'])
def test_subcent_layers_conserve_original_value_on_partial_depletion(method):
    layer = SimpleNamespace(id='grams', initial_quantity=1500, remaining_quantity=1500,
        unit_cost_cents=0, initial_value_cents=299, remaining_value_cents=299,
        lot_number='weighed', expiration_date=None, quarantined=False, received_at=datetime.now(timezone.utc))
    recognized = 0
    for quantity in (250, 250, 1000):
        cost, used = consume([layer], quantity, method)
        assert cost > 0 and sum(item['costCents'] for item in used) == cost
        recognized += cost
        assert recognized + layer.remaining_value_cents == 299
    assert recognized == 299 and layer.remaining_quantity == 0
