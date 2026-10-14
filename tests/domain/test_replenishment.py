import pytest
from src.domain.replenishment import suggest


@pytest.mark.parametrize('available,incoming,sales,expected', [
    (0, 0, 0, 18), (4, 0, 0, 12), (4, 6, 0, 0), (0, 10, 0, 0),
    (4, 6, 60, 12), (15, 0, 0, 0), (0, 0, 300, 72),
])
def test_pack_rounding_incoming_and_lead_time_demand(available, incoming, sales, expected):
    result = suggest(available, incoming, sales, 5, 15, 7, 1, 6, 10)
    assert result['suggestedQuantity'] == expected
    assert result['leadTimeDemand'] == (sales * 7 + 29) // 30
