from types import SimpleNamespace
from fractions import Fraction
import pytest
from src.domain.uom import (
    base_quantity,
    factor,
    cost_groups,
    receipt_value,
    decimal_fraction,
    decimal_text,
    MAX_QUANTITY,
)
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


def test_decimal_fraction_valid_and_invalid():
    # Integer input converts properly
    assert decimal_fraction(42) == Fraction(42, 1)
    assert decimal_fraction("12.3456") == Fraction(123456, 10000)
    assert decimal_fraction("0") == Fraction(0, 1)

    # Invalid string inputs
    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction("1.1234567")  # 7 decimal places

    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction("-5")  # Negative string

    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction("  10  ")  # Whitespace

    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction("")  # Empty string

    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction("1" * 33)  # Exceeds max length of 32

    with pytest.raises(ValueError, match="at most six decimal places"):
        decimal_fraction(3.14)  # Float not allowed


def test_factor_edge_cases():
    # Standard category with unknown unit
    weight_config = SimpleNamespace(category="weight", base_unit="g", rules=[])
    with pytest.raises(ValueError, match="Unknown or incompatible standard unit"):
        factor(weight_config, "unknown_unit")

    # Standard category with unknown base_unit
    invalid_base_config = SimpleNamespace(category="weight", base_unit="invalid_base", rules=[])
    with pytest.raises(ValueError, match="Unknown or incompatible standard unit"):
        factor(invalid_base_config, "kg")

    # Standard category conversion factor calculation
    vol_config = SimpleNamespace(category="volume", base_unit="ml", rules=[])
    assert factor(vol_config, "l") == Fraction(1000, 1)
    assert factor(vol_config, "ml") == Fraction(1, 1)

    # Non-standard category where unit equals base_unit
    discrete_config = SimpleNamespace(category="discrete", base_unit="ea", rules=[])
    assert factor(discrete_config, "ea") == Fraction(1)

    # Non-standard category missing rule
    with pytest.raises(ValueError, match="No product-specific conversion rule"):
        factor(discrete_config, "box")


def test_cost_groups_edge_cases():
    # None value returns single tuple with None cost
    assert cost_groups(10, None) == [(10, None)]

    # Normal exact calculation with rounding
    assert cost_groups(3, 100) == [(3, 33)]

    # Exceeding MAX_QUANTITY * quantity limit
    with pytest.raises(ValueError, match="Converted unit cost exceeds the supported limit"):
        cost_groups(1, MAX_QUANTITY + 1)


def test_decimal_text_conversions():
    # Whole integer denominator 1
    assert decimal_text(Fraction(5, 1)) == "5"
    assert decimal_text(Fraction(0, 1)) == "0"

    # Terminating decimals with divisors 2 and 5
    assert decimal_text(Fraction(1, 2)) == "0.5"
    assert decimal_text(Fraction(1, 4)) == "0.25"
    assert decimal_text(Fraction(1, 8)) == "0.125"
    assert decimal_text(Fraction(3, 5)) == "0.6"
    assert decimal_text(Fraction(7, 20)) == "0.35"
    assert decimal_text(Fraction(125, 100)) == "1.25"

    # Non-terminating decimal representations return structured dict
    assert decimal_text(Fraction(1, 3)) == {"numerator": "1", "denominator": "3"}
    assert decimal_text(Fraction(2, 7)) == {"numerator": "2", "denominator": "7"}
