import pytest
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import patch
from src.domain.product.entity import Product, generate_uuid
from src.domain.product.value_objects import SKU, Money

def test_generate_uuid():
    """Test that generate_uuid returns a valid UUID string."""
    val = generate_uuid()
    assert isinstance(val, str)
    # This will raise ValueError if not a valid UUID
    uuid.UUID(val)

def test_product_default_instantiation():
    """Test default values of a Product entity."""
    product = Product()

    # Check default id
    assert isinstance(product.id, str)
    uuid.UUID(product.id)

    # Check default SKU
    assert isinstance(product.sku, SKU)
    assert product.sku.value == 'DUMMY-SKU'

    # Check default name and description
    assert product.name == ''
    assert product.description is None

    # Check default price
    assert isinstance(product.price, Money)
    assert product.price.amount == Decimal('0.00')
    assert product.price.currency == 'USD'

    # Check default timestamps
    assert isinstance(product.created_at, datetime)
    assert product.created_at.tzinfo == timezone.utc
    assert isinstance(product.updated_at, datetime)
    assert product.updated_at.tzinfo == timezone.utc

def test_product_create_class_method():
    """Test creating a Product using the create classmethod."""
    product = Product.create(
        sku="TEST-SKU",
        name="Test Product",
        description="A great test product",
        price_amount="99.99"
    )

    assert isinstance(product.id, str)
    assert product.sku.value == "TEST-SKU"
    assert product.name == "Test Product"
    assert product.description == "A great test product"
    assert product.price.amount == Decimal("99.99")

def test_product_create_without_description():
    """Test creating a Product without a description."""
    product = Product.create(
        sku="TEST-SKU",
        name="Test Product",
        description=None,
        price_amount="49.99"
    )

    assert product.description is None
    assert product.price.amount == Decimal("49.99")

@patch('src.domain.product.entity.datetime')
def test_product_update_price(mock_datetime):
    """Test updating the price of a Product and verifying updated_at changes."""
    # Mock datetime to control time for updated_at
    mock_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    mock_datetime.now.return_value = mock_now

    product = Product.create(
        sku="TEST-SKU",
        name="Test Product",
        description=None,
        price_amount="10.00"
    )

    # Reset mock and update time
    mock_later = datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
    mock_datetime.now.return_value = mock_later

    product.update_price("15.00")

    assert product.price.amount == Decimal("15.00")
    assert product.updated_at == mock_later
