import pytest
from decimal import Decimal
from src.domain.product.value_objects import SKU, Money
from src.domain.product.entity import Product
from src.domain.product.exceptions import InvalidSKUError, InvalidPriceError

def test_sku_creation_success():
    sku = SKU('PROD-123')
    assert sku.value == 'PROD-123'

def test_sku_creation_failure():
    with pytest.raises(InvalidSKUError):
        SKU('invalid sku format!')

def test_money_creation_success():
    money = Money(Decimal('10.50'))
    assert money.amount == Decimal('10.50')
    assert money.currency == 'USD'

def test_money_creation_failure():
    with pytest.raises(InvalidPriceError):
        Money(Decimal('-10.00'))

def test_product_entity_creation():
    product = Product.create('SKU-1', 'Product 1', 'Desc', '99.99')
    assert product.sku.value == 'SKU-1'
    assert product.price.amount == Decimal('99.99')

