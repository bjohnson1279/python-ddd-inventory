"""Integer-unit reorder suggestions, with explicit incoming stock and pack rounding."""
def suggest(available, incoming, sold_30_days, reorder_point, target, lead_days, safety, pack, minimum):
    demand = (sold_30_days * lead_days + 29) // 30
    point = max(reorder_point, demand + safety)
    target = max(target, point)
    projected = available + incoming
    needed = max(0, target - projected) if projected <= point else 0
    quantity = ((max(needed, minimum) + pack - 1) // pack) * pack if needed else 0
    if quantity > 2_147_483_647:
        raise ValueError('Suggested order exceeds supported quantity limit')
    return {'availableQuantity': available, 'incomingQuantity': incoming,
            'projectedQuantity': projected, 'soldLast30Days': sold_30_days,
            'leadTimeDemand': demand, 'effectiveReorderPoint': point,
            'targetStock': target, 'suggestedQuantity': quantity}
