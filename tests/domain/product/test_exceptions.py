import pytest
from src.domain.product.exceptions import (
    DomainException,
    InvalidSKUError,
    InvalidPriceError,
    ProductNotFoundError
)

def test_domain_exception_instantiation():
    """Test that base DomainException can be instantiated and carries a message."""
    err = DomainException("Base domain error")
    assert str(err) == "Base domain error"
    assert isinstance(err, Exception)

def test_invalid_sku_error_instantiation():
    """Test InvalidSKUError inherits from DomainException and sets message."""
    err = InvalidSKUError("Invalid SKU format")
    assert str(err) == "Invalid SKU format"
    assert isinstance(err, DomainException)
    assert issubclass(InvalidSKUError, DomainException)

def test_invalid_price_error_instantiation():
    """Test InvalidPriceError inherits from DomainException and sets message."""
    err = InvalidPriceError("Price cannot be negative")
    assert str(err) == "Price cannot be negative"
    assert isinstance(err, DomainException)
    assert issubclass(InvalidPriceError, DomainException)

def test_product_not_found_error_instantiation():
    """Test ProductNotFoundError inherits from DomainException and sets message."""
    err = ProductNotFoundError("Product '123' not found")
    assert str(err) == "Product '123' not found"
    assert isinstance(err, DomainException)
    assert issubclass(ProductNotFoundError, DomainException)

def test_exceptions_can_be_raised_and_caught():
    """Test that these exceptions can actually be raised and caught correctly."""
    with pytest.raises(DomainException) as exc_info:
        raise InvalidSKUError("SKU must be valid")
    assert str(exc_info.value) == "SKU must be valid"
    assert isinstance(exc_info.value, InvalidSKUError)

    with pytest.raises(ProductNotFoundError) as exc_info:
        raise ProductNotFoundError("Not found")
    assert str(exc_info.value) == "Not found"
