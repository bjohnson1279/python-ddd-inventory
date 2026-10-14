"""Exact decimal conversions. The inventory ledger contains integral base units."""
import re
from fractions import Fraction
from src.domain.product.layer_costing import half_up

MAX_QUANTITY = 2_147_483_647
# Match the shared PHP standard-unit table, expressed as exact decimals rather
# than binary floats. Factors are relative to grams or milliliters.
STANDARD_UNITS = {
    'weight': {'g': '1', 'kg': '1000', 'oz': '28.3495', 'lb': '453.592'},
    'volume': {'ml': '1', 'l': '1000', 'fl_oz': '29.5735', 'cup': '236.588', 'gal': '3785.41'},
}


def decimal_fraction(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r'\d+(?:\.\d{1,6})?', value):
        raise ValueError('Use an integer or a decimal string with at most six decimal places')
    return Fraction(value)


def factor(config, unit):
    if config.category in STANDARD_UNITS:
        standards = STANDARD_UNITS[config.category]
        if unit not in standards or config.base_unit not in standards:
            raise ValueError('Unknown or incompatible standard unit')
        return Fraction(standards[unit]) / Fraction(standards[config.base_unit])
    if unit == config.base_unit:
        return Fraction(1)
    rules = {rule['unit']: rule['factorToBase'] for rule in config.rules}
    if unit not in rules:
        raise ValueError('No product-specific conversion rule for unit')
    return decimal_fraction(rules[unit])


def base_quantity(config, amount, unit):
    quantity = decimal_fraction(amount) * factor(config, unit)
    if quantity.denominator != 1:
        raise ValueError('Quantity cannot be represented as integral base units; choose a smaller base unit')
    if not 0 < quantity <= MAX_QUANTITY:
        raise ValueError('Quantity is outside the supported base-unit limit')
    return int(quantity)


def receipt_value(amount, unit_cost):
    value = decimal_fraction(amount) * unit_cost
    return half_up(value.numerator, value.denominator)


def cost_groups(quantity, value):
    """One exact-value layer; displayed unit cost is rounded to integer cents."""
    if value is None:
        return [(quantity, None)]
    cost = half_up(value, quantity)
    if value > MAX_QUANTITY * quantity:
        raise ValueError('Converted unit cost exceeds the supported limit')
    return [(quantity, cost)]


def decimal_text(value):
    if value.denominator == 1:
        return str(value.numerator)
    denominator = value.denominator
    for divisor in (2, 5):
        while denominator % divisor == 0:
            denominator //= divisor
    if denominator == 1:
        integer, remainder = divmod(value.numerator, value.denominator)
        digits = ''
        while remainder:
            digit, remainder = divmod(remainder * 10, value.denominator)
            digits += str(digit)
        return str(integer) + '.' + digits
    # Non-terminating results must not pretend to be exact decimal quantities.
    return {'numerator': str(value.numerator), 'denominator': str(value.denominator)}
