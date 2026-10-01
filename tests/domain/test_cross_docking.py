import pytest
from src.domain.product.cross_docking import InboundASN, OutboundOrder, CrossDockingEngine

def test_engine_initialization():
    engine = CrossDockingEngine()
    assert engine.pending_outbound == []

def test_register_outbound_order():
    engine = CrossDockingEngine()
    order = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    engine.register_outbound_order(order)

    assert len(engine.pending_outbound) == 1
    assert engine.pending_outbound[0].order_id == "ORD-001"
    assert engine.pending_outbound[0].sku == "SKU-123"
    assert engine.pending_outbound[0].quantity == 10
    assert engine.pending_outbound[0].customer_id == "CUST-1"

def test_process_inbound_asn_exact_match():
    engine = CrossDockingEngine()
    order = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    engine.register_outbound_order(order)

    asn = InboundASN(asn_id="ASN-001", supplier_id="SUP-1", sku="SKU-123", quantity=10, destination_dock="DOCK-A")
    assignments = engine.process_inbound_asn(asn)

    assert assignments == {"ORD-001": 10}
    assert len(engine.pending_outbound) == 0

def test_process_inbound_asn_partial_fulfillment():
    engine = CrossDockingEngine()
    order = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    engine.register_outbound_order(order)

    asn = InboundASN(asn_id="ASN-001", supplier_id="SUP-1", sku="SKU-123", quantity=6, destination_dock="DOCK-A")
    assignments = engine.process_inbound_asn(asn)

    assert assignments == {"ORD-001": 6}
    assert len(engine.pending_outbound) == 1
    assert engine.pending_outbound[0].quantity == 4

def test_process_inbound_asn_multiple_orders():
    engine = CrossDockingEngine()
    order1 = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    order2 = OutboundOrder(order_id="ORD-002", sku="SKU-123", quantity=15, customer_id="CUST-2")
    engine.register_outbound_order(order1)
    engine.register_outbound_order(order2)

    asn = InboundASN(asn_id="ASN-001", supplier_id="SUP-1", sku="SKU-123", quantity=20, destination_dock="DOCK-A")
    assignments = engine.process_inbound_asn(asn)

    assert assignments == {"ORD-001": 10, "ORD-002": 10}
    assert len(engine.pending_outbound) == 1
    assert engine.pending_outbound[0].order_id == "ORD-002"
    assert engine.pending_outbound[0].quantity == 5

def test_process_inbound_asn_unmatched_sku():
    engine = CrossDockingEngine()
    order = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    engine.register_outbound_order(order)

    asn = InboundASN(asn_id="ASN-001", supplier_id="SUP-1", sku="SKU-999", quantity=20, destination_dock="DOCK-A")
    assignments = engine.process_inbound_asn(asn)

    assert assignments == {}
    assert len(engine.pending_outbound) == 1
    assert engine.pending_outbound[0].quantity == 10

def test_process_inbound_asn_over_fulfillment():
    engine = CrossDockingEngine()
    order = OutboundOrder(order_id="ORD-001", sku="SKU-123", quantity=10, customer_id="CUST-1")
    engine.register_outbound_order(order)

    asn = InboundASN(asn_id="ASN-001", supplier_id="SUP-1", sku="SKU-123", quantity=50, destination_dock="DOCK-A")
    assignments = engine.process_inbound_asn(asn)

    assert assignments == {"ORD-001": 10}
    assert len(engine.pending_outbound) == 0
