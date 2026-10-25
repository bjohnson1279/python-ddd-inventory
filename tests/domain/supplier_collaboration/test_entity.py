import pytest
from datetime import datetime, timezone
from src.domain.supplier_collaboration.entity import PurchaseOrder

def test_purchase_order_acknowledge_success():
    po = PurchaseOrder(tenant_id="t1", supplier_id="s1", status="ISSUED")
    now = datetime.now(timezone.utc)
    po.acknowledge(acknowledged_at=now)
    assert po.status == "ACKNOWLEDGED"
    assert po.acknowledged_date == now

def test_purchase_order_acknowledge_invalid_status():
    po = PurchaseOrder(tenant_id="t1", supplier_id="s1", status="SHIPPED")
    with pytest.raises(ValueError, match="Cannot acknowledge purchase order in status 'SHIPPED'"):
        po.acknowledge()

def test_purchase_order_mark_as_shipped_success():
    po = PurchaseOrder(tenant_id="t1", supplier_id="s1", status="ISSUED")
    po.mark_as_shipped()
    assert po.status == "SHIPPED"

    po_ack = PurchaseOrder(tenant_id="t1", supplier_id="s1", status="ACKNOWLEDGED")
    po_ack.mark_as_shipped()
    assert po_ack.status == "SHIPPED"

def test_purchase_order_mark_as_shipped_invalid_status():
    po = PurchaseOrder(tenant_id="t1", supplier_id="s1", status="RECEIVED")
    with pytest.raises(ValueError, match="Cannot mark purchase order as shipped from status 'RECEIVED'"):
        po.mark_as_shipped()
