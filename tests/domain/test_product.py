import pytest
from decimal import Decimal
from src.domain.product.value_objects import SKU, Money
from src.domain.product.entity import Product
from src.domain.product.exceptions import InvalidSKUError, InvalidPriceError


def test_product_entity_creation():
    product = Product.create('SKU-1', 'Product 1', 'Desc', '99.99')
    assert product.sku.value == 'SKU-1'
    assert product.price.amount == Decimal('99.99')

