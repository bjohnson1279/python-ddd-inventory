import pytest
from datetime import datetime
from src.domain.supplier_collaboration.value_objects import Email, InvalidEmailError, POStatus, ASNStatus
from src.domain.supplier_collaboration.entity import Supplier, AdvanceShippingNotice

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

def test_asnstatus_enum_values():
    assert ASNStatus.SUBMITTED == "SUBMITTED"
    assert ASNStatus.RECEIVED == "RECEIVED"
    assert ASNStatus.DISCREPANCY == "DISCREPANCY"

def test_asn_entity_with_status_string():
    asn = AdvanceShippingNotice(
        po_id="po-1",
        supplier_id="sup-1",
        tracking_number="TRK123",
        estimated_delivery_date=datetime.now(),
        status="RECEIVED"
    )
    assert isinstance(asn.status, ASNStatus)
    assert asn.status == ASNStatus.RECEIVED

def test_asn_entity_with_status_enum():
    asn = AdvanceShippingNotice(
        po_id="po-1",
        supplier_id="sup-1",
        tracking_number="TRK123",
        estimated_delivery_date=datetime.now(),
        status=ASNStatus.DISCREPANCY
    )
    assert isinstance(asn.status, ASNStatus)
    assert asn.status == ASNStatus.DISCREPANCY

def test_asn_entity_with_default_status():
    asn = AdvanceShippingNotice(
        po_id="po-1",
        supplier_id="sup-1",
        tracking_number="TRK123",
        estimated_delivery_date=datetime.now()
    )
    assert isinstance(asn.status, ASNStatus)
    assert asn.status == ASNStatus.SUBMITTED

def test_asn_entity_with_invalid_status():
    with pytest.raises(ValueError):
        AdvanceShippingNotice(
            po_id="po-1",
            supplier_id="sup-1",
            tracking_number="TRK123",
            estimated_delivery_date=datetime.now(),
            status="INVALID_STATUS"
        )
