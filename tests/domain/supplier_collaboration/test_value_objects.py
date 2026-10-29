import pytest
from src.domain.supplier_collaboration.value_objects import Email, InvalidEmailError, POStatus
from src.domain.supplier_collaboration.entity import Supplier

def test_email_valid():
    email = Email("supplier@example.com")
    assert email.value == "supplier@example.com"

def test_email_invalid_format():
    with pytest.raises(InvalidEmailError, match="Invalid email format"):
        Email("invalid-email")

def test_email_empty():
    with pytest.raises(InvalidEmailError, match="Email must be a non-empty string"):
        Email("")

def test_email_non_string():
    with pytest.raises(InvalidEmailError, match="Email must be a non-empty string"):
        Email(123)

def test_supplier_entity_with_email():
    supplier = Supplier(
        tenant_id="t1",
        name="Acme Corp",
        contact_email="vendor@acme.com"
    )
    assert isinstance(supplier.contact_email, Email)
    assert supplier.contact_email.value == "vendor@acme.com"

def test_supplier_entity_with_invalid_email():
    with pytest.raises(InvalidEmailError):
        Supplier(
            tenant_id="t1",
            name="Acme Corp",
            contact_email="bademail"
        )

def test_postatus_enum_values():
    assert POStatus.ISSUED == "ISSUED"
    assert POStatus.ACKNOWLEDGED == "ACKNOWLEDGED"
    assert POStatus.SHIPPED == "SHIPPED"
    assert POStatus.RECEIVED == "RECEIVED"
