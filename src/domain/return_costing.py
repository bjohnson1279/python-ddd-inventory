from src.domain.product.layer_costing import half_up


def allocate_cents(total, quantities):
    """Deterministic largest-remainder allocation, conserving original cents."""
    count = sum(quantities)
    if total is None:
        return [None] * len(quantities)
    values = [total * q // count for q in quantities]
    ranks = sorted(range(len(quantities)), key=lambda i: (-(total * quantities[i] % count), i))
    for i in ranks[:total - sum(values)]:
        values[i] += 1
    return values


def returned_cents(original_value, original_quantity, already_returned, quantity):
    if quantity <= 0 or already_returned < 0 or already_returned + quantity > original_quantity:
        raise ValueError('Return exceeds original quantity')
    if original_value is None:
        return None
    return half_up(original_value * (already_returned + quantity), original_quantity) - half_up(
        original_value * already_returned, original_quantity)
