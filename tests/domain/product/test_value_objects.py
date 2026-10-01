import pytest
from decimal import Decimal
from src.domain.product.value_objects import SKU, Money
from src.domain.product.exceptions import InvalidSKUError, InvalidPriceError

def test_sku_creation_success():
    sku = SKU('PROD-123')
    assert sku.value == 'PROD-123'

def test_sku_creation_failure_format():
    with pytest.raises(InvalidSKUError, match='SKU must contain only uppercase letters, numbers, and hyphens'):
        SKU('invalid sku format!')

def test_sku_creation_failure_empty_string():
    with pytest.raises(InvalidSKUError, match='SKU must be a non-empty string'):
        SKU('')

def test_sku_creation_failure_none():
    with pytest.raises(InvalidSKUError, match='SKU must be a non-empty string'):
        SKU(None)

def test_sku_creation_failure_wrong_type():
    with pytest.raises(InvalidSKUError, match='SKU must be a non-empty string'):
        SKU(123)

def test_money_creation_success():
    money = Money(Decimal('10.50'))
    assert money.amount == Decimal('10.50')
    assert money.currency == 'USD'

def test_money_creation_failure_negative_amount():
    with pytest.raises(InvalidPriceError, match='Price cannot be negative'):
        Money(Decimal('-10.00'))

def test_money_creation_failure_invalid_currency_length_short():
    with pytest.raises(InvalidPriceError, match='Currency must be a 3-letter ISO code'):
        Money(Decimal('10.00'), 'US')

def test_money_creation_failure_invalid_currency_length_long():
    with pytest.raises(InvalidPriceError, match='Currency must be a 3-letter ISO code'):
        Money(Decimal('10.00'), 'USDD')
