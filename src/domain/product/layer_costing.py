"""Deterministic layer selection and integer-cent valuation.

WAC is a moving average per SKU/location. Allocation of rounding pennies to
remaining layers conserves receipt value minus recognized COGS exactly.
"""
from datetime import datetime, timezone
from enum import Enum

MAX_SAFE_CENTS = 9_007_199_254_740_991


def validate_total_value(value):
    if not 0 <= value <= MAX_SAFE_CENTS:
        raise ValueError('Inventory value exceeds the supported integer-cent limit')


class CostingMethod(str, Enum):
    FIFO = 'FIFO'
    LIFO = 'LIFO'
    WAC = 'WAC'
    FEFO = 'FEFO'


def normalize_method(value):
    name = value.upper()
    return CostingMethod('WAC' if name == 'WEIGHTED_AVERAGE_COST' else name)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def half_up(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def eligible_layers(layers, method, now=None, lot_number=None):
    now = now or datetime.now(timezone.utc)
    usable = [layer for layer in layers if layer.remaining_quantity > 0
              and not layer.quarantined
              and (layer.expiration_date is None or utc(layer.expiration_date) > utc(now))
              and (lot_number is None or layer.lot_number == lot_number)]
    if method == CostingMethod.FEFO:
        return sorted(usable, key=lambda layer: (
            utc(layer.expiration_date) if layer.expiration_date else datetime.max.replace(tzinfo=timezone.utc),
            utc(layer.received_at), layer.id))
    return sorted(usable, key=lambda layer: (utc(layer.received_at), layer.id),
                  reverse=method == CostingMethod.LIFO)


def redistribute_value(layers, total_value):
    """Largest-remainder distribution; stable IDs break equal remainder ties."""
    total_quantity = sum(layer.remaining_quantity for layer in layers)
    if total_quantity == 0:
        if total_value:
            raise ValueError('Cannot assign value to empty inventory')
        for layer in layers:
            layer.remaining_value_cents = 0
        return
    ranked = []
    assigned = 0
    for layer in layers:
        value, remainder = divmod(total_value * layer.remaining_quantity, total_quantity)
        layer.remaining_value_cents = value
        assigned += value
        if layer.remaining_quantity:
            ranked.append((remainder, layer.id, layer))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for _, _, layer in ranked[:total_value - assigned]:
        layer.remaining_value_cents += 1


def consume(layers, quantity, method, now=None, lot_number=None):
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError('Quantity must be a positive integer')
    method = normalize_method(method)
    selected = eligible_layers(layers, method, now, lot_number)
    if sum(layer.remaining_quantity for layer in selected) < quantity:
        raise ValueError('Insufficient eligible cost layers for requested quantity or lot')
    active = [layer for layer in layers if layer.remaining_quantity]
    complete = all(layer.remaining_value_cents is not None for layer in active)
    if method == CostingMethod.WAC and not complete:
        raise ValueError('Price unpriced stock before weighted-average dispatch')
    total_value = sum(layer.remaining_value_cents or 0 for layer in active)
    total_quantity = sum(layer.remaining_quantity for layer in active)
    cost = half_up(total_value * quantity, total_quantity) if method == CostingMethod.WAC else 0
    remaining = quantity
    consumptions = []
    for layer in selected:
        if remaining == 0:
            break
        taken = min(remaining, layer.remaining_quantity)
        layer.remaining_quantity -= taken
        remaining -= taken
        known = layer.unit_cost_cents is not None
        if method != CostingMethod.WAC:
            if known:
                cost = None if cost is None else cost + taken * layer.unit_cost_cents
                layer.remaining_value_cents = layer.remaining_quantity * layer.unit_cost_cents
            else:
                cost = None
                if layer.remaining_quantity == 0:
                    layer.remaining_value_cents = 0
        consumptions.append({'layerId': layer.id, 'lotNumber': layer.lot_number, 'quantity': taken})
    if method == CostingMethod.WAC:
        redistribute_value(layers, total_value - cost)
    return cost, consumptions
