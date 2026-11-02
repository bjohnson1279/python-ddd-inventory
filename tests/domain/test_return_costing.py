import pytest
from src.domain.return_costing import allocate_cents, returned_cents


def test_wac_pennies_are_conserved_across_layer_and_partial_returns():
    assert allocate_cents(5, [2, 1]) == [3, 2]
    assert [returned_cents(5, 3, previous, 1) for previous in range(3)] == [2, 1, 2]
    assert sum(returned_cents(5, 3, previous, 1) for previous in range(3)) == 5
    assert returned_cents(None, 3, 0, 1) is None


def test_allocate_cents_edge_cases():
    # Empty quantities list
    assert allocate_cents(100, []) == []

    # None total returns None for each quantity
    assert allocate_cents(None, [1, 2, 3]) == [None, None, None]
    assert allocate_cents(None, []) == []

    # Zero total returns zero for each quantity
    assert allocate_cents(0, [10, 20]) == [0, 0]

    # Equal division without remainder
    assert allocate_cents(100, [1, 1]) == [50, 50]
    assert allocate_cents(300, [1, 1, 1]) == [100, 100, 100]

    # Remainder distribution tie-breaking by index (lower index gets the remainder penny)
    # Total 1 cent across 3 equal quantities: remainder 1 goes to index 0
    assert allocate_cents(1, [1, 1, 1]) == [1, 0, 0]
    # Total 2 cents across 3 equal quantities: remainder 2 goes to index 0 and 1
    assert allocate_cents(2, [1, 1, 1]) == [1, 1, 0]

    # Asymmetric quantities with remainder
    # Total 10 cents, quantities [3, 1]:
    # 10 * 3 // 4 = 7, remainder 10*3%4 = 2
    # 10 * 1 // 4 = 2, remainder 10*1%4 = 2
    # Tie in remainder 2, index 0 gets extra penny: [8, 2]
    assert allocate_cents(10, [3, 1]) == [8, 2]
    assert sum(allocate_cents(10, [3, 1])) == 10


def test_returned_cents_validation_and_failure_modes():
    # quantity <= 0
    with pytest.raises(ValueError, match='Return exceeds original quantity'):
        returned_cents(100, 10, 0, 0)
    with pytest.raises(ValueError, match='Return exceeds original quantity'):
        returned_cents(100, 10, 0, -1)

    # already_returned < 0
    with pytest.raises(ValueError, match='Return exceeds original quantity'):
        returned_cents(100, 10, -1, 1)

    # already_returned + quantity > original_quantity
    with pytest.raises(ValueError, match='Return exceeds original quantity'):
        returned_cents(100, 10, 5, 6)
    with pytest.raises(ValueError, match='Return exceeds original quantity'):
        returned_cents(100, 10, 11, 1)


def test_returned_cents_full_return():
    # Full return in a single transaction
    assert returned_cents(1000, 5, 0, 5) == 1000
    assert returned_cents(0, 5, 0, 5) == 0
    assert returned_cents(None, 5, 0, 5) is None


def test_returned_cents_multi_step_partial_returns_conservation():
    # 7 cents across 4 units, returned 1 by 1
    original_value = 7
    original_qty = 4
    returned_portions = []
    accumulated = 0
    for _ in range(original_qty):
        part = returned_cents(original_value, original_qty, accumulated, 1)
        returned_portions.append(part)
        accumulated += 1

    assert sum(returned_portions) == original_value
    assert accumulated == original_qty

    # Return in unequal batch chunks: 2 then 2
    part1 = returned_cents(original_value, original_qty, 0, 2)
    part2 = returned_cents(original_value, original_qty, 2, 2)
    assert part1 + part2 == original_value
