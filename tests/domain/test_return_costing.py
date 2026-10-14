from src.domain.return_costing import allocate_cents, returned_cents


def test_wac_pennies_are_conserved_across_layer_and_partial_returns():
    assert allocate_cents(5, [2, 1]) == [3, 2]
    assert [returned_cents(5, 3, previous, 1) for previous in range(3)] == [2, 1, 2]
    assert sum(returned_cents(5, 3, previous, 1) for previous in range(3)) == 5
    assert returned_cents(None, 3, 0, 1) is None
