import pytest
from datetime import datetime, timedelta
from src.domain.supplier_collaboration.entity import PurchaseOrder, AdvanceShippingNotice, ASNLineItem
from src.domain.supplier_collaboration.services import ASNSubmissionService, OTIFCalculationService

def test_asn_submission_valid():
    service = ASNSubmissionService()
    po = PurchaseOrder(tenant_id="t1", supplier_id="sup1", status="ACKNOWLEDGED")
    asn = AdvanceShippingNotice(
        po_id=po.id,
        supplier_id="sup1",
        tracking_number="TRK123",
        estimated_delivery_date=datetime.utcnow(),
        items=[ASNLineItem(sku="SKU1", shipped_quantity=100)]
    )
    
    result_asn = service.validate_and_submit_asn(po, asn)
    
    assert result_asn.status == "SUBMITTED"
    assert po.status == "SHIPPED"

def test_asn_submission_invalid_status():
    service = ASNSubmissionService()
    po = PurchaseOrder(tenant_id="t1", supplier_id="sup1", status="SHIPPED")
    asn = AdvanceShippingNotice(
        po_id=po.id,
        supplier_id="sup1",
        tracking_number="TRK123",
        estimated_delivery_date=datetime.utcnow(),
        items=[ASNLineItem(sku="SKU1", shipped_quantity=100)]
    )
    
    with pytest.raises(ValueError, match="Cannot submit ASN for PO"):
        service.validate_and_submit_asn(po, asn)

def test_otif_calculation_success():
    service = OTIFCalculationService()
    now = datetime.utcnow()
    asn = AdvanceShippingNotice(
        po_id="po1",
        supplier_id="sup1",
        tracking_number="TRK",
        estimated_delivery_date=now + timedelta(days=2),
        items=[ASNLineItem(sku="SKU1", shipped_quantity=50)]
    )
    
    actual_receipt_date = now + timedelta(days=1)
    actual_quantities = {"SKU1": 50}
    
    result = service.calculate_otif(asn, actual_receipt_date, actual_quantities)
    
    assert result["on_time"] is True
    assert result["in_full"] is True
    assert result["otif_success"] is True
    assert result["defect_rate_percentage"] == 0.0

def test_otif_calculation_late_and_short():
    service = OTIFCalculationService()
    now = datetime.utcnow()
    asn = AdvanceShippingNotice(
        po_id="po1",
        supplier_id="sup1",
        tracking_number="TRK",
        estimated_delivery_date=now - timedelta(days=1),
        items=[ASNLineItem(sku="SKU1", shipped_quantity=50)]
    )
    
    actual_receipt_date = now + timedelta(days=1) # 2 days late
    actual_quantities = {"SKU1": 40} # Short 10
    
    result = service.calculate_otif(asn, actual_receipt_date, actual_quantities)
    
    assert result["on_time"] is False
    assert result["in_full"] is False
    assert result["otif_success"] is False
    assert result["defect_rate_percentage"] == 100.0
