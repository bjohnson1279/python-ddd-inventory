import pytest
from datetime import datetime, timezone, timedelta
from src.domain.product.lot_tracking import Lot, LotTrackingEngine
from src.domain.product.value_objects import SKU, LotStatus
from src.domain.product.exceptions import InvalidSKUError

def test_lot_initialization():
    now = datetime.now(timezone.utc)
    lot = Lot(
        id="lot-1",
        sku="SKU-1",
        supplier_id="SUP-1",
        expiration_date=now + timedelta(days=30)
    )
    assert lot.id == "lot-1"
    assert isinstance(lot.sku, SKU)
    assert lot.sku == SKU("SKU-1")
    assert lot.sku.value == "SKU-1"
    assert lot.supplier_id == "SUP-1"
    assert lot.status == "ACTIVE"
    assert isinstance(lot.received_at, datetime)
    assert lot.received_at.tzinfo == timezone.utc

def test_register_lot():
    engine = LotTrackingEngine()
    lot = Lot(
        id="lot-1",
        sku="SKU-1",
        supplier_id="SUP-1",
        expiration_date=datetime.now(timezone.utc) + timedelta(days=30)
    )
    engine.register_lot(lot)
    assert len(engine._lots) == 1
    assert engine._lots[0] == lot

def test_enforce_fefo_allocation():
    engine = LotTrackingEngine()
    now = datetime.now(timezone.utc)

    lot1 = Lot(id="1", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=10))
    lot2 = Lot(id="2", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=5))
    lot3 = Lot(id="3", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=20))
    lot4 = Lot(id="4", sku="SKU-2", supplier_id="SUP-1", expiration_date=now + timedelta(days=1)) # Different SKU
    lot5 = Lot(id="5", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=2), status="QUARANTINED") # Not active

    engine.register_lot(lot1)
    engine.register_lot(lot2)
    engine.register_lot(lot3)
    engine.register_lot(lot4)
    engine.register_lot(lot5)

    allocated = engine.enforce_fefo_allocation("SKU-1", 10)

    assert len(allocated) == 3
    assert allocated[0].id == "2" # Expires in 5 days
    assert allocated[1].id == "1" # Expires in 10 days
    assert allocated[2].id == "3" # Expires in 20 days

def test_auto_quarantine_expired_lots():
    engine = LotTrackingEngine()
    now = datetime.now(timezone.utc)

    lot1 = Lot(id="1", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=10)) # Future
    lot2 = Lot(id="2", sku="SKU-1", supplier_id="SUP-1", expiration_date=now - timedelta(days=5)) # Expired
    lot3 = Lot(id="3", sku="SKU-1", supplier_id="SUP-1", expiration_date=now - timedelta(days=1)) # Expired
    lot4 = Lot(id="4", sku="SKU-1", supplier_id="SUP-1", expiration_date=now - timedelta(days=2), status="RECALLED") # Already recalled

    engine.register_lot(lot1)
    engine.register_lot(lot2)
    engine.register_lot(lot3)
    engine.register_lot(lot4)

    quarantined_count = engine.auto_quarantine_expired_lots()

    assert quarantined_count == 2
    assert engine._lots[0].status == "ACTIVE"
    assert engine._lots[1].status == "QUARANTINED"
    assert engine._lots[2].status == "QUARANTINED"
    assert engine._lots[3].status == "RECALLED"

def test_trigger_supplier_recall():
    engine = LotTrackingEngine()
    now = datetime.now(timezone.utc)

    lot1 = Lot(id="1", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=10))
    lot2 = Lot(id="2", sku="SKU-1", supplier_id="SUP-2", expiration_date=now + timedelta(days=10)) # Different supplier
    lot3 = Lot(id="3", sku="SKU-2", supplier_id="SUP-1", expiration_date=now + timedelta(days=10)) # Different SKU
    lot4 = Lot(id="4", sku="SKU-1", supplier_id="SUP-1", expiration_date=now + timedelta(days=10), status="QUARANTINED") # Same supplier and SKU, diff status

    engine.register_lot(lot1)
    engine.register_lot(lot2)
    engine.register_lot(lot3)
    engine.register_lot(lot4)

    recalled_lots = engine.trigger_supplier_recall("SUP-1", "SKU-1")

    assert len(recalled_lots) == 2
    assert recalled_lots[0].id == "1"
    assert recalled_lots[1].id == "4"

    assert engine._lots[0].status == "RECALLED"
    assert engine._lots[1].status == "ACTIVE"
    assert engine._lots[2].status == "ACTIVE"
    assert engine._lots[3].status == "RECALLED"

def test_lot_with_sku_value_object():
    now = datetime.now(timezone.utc)
    sku_obj = SKU("WIDGET-01")
    lot = Lot(
        id="lot-vo",
        sku=sku_obj,
        supplier_id="SUP-1",
        expiration_date=now + timedelta(days=15)
    )
    assert lot.sku == sku_obj
    assert lot.sku.value == "WIDGET-01"

def test_lot_invalid_sku_raises():
    now = datetime.now(timezone.utc)
    with pytest.raises(InvalidSKUError):
        Lot(
            id="lot-invalid",
            sku="invalid sku with spaces!",
            supplier_id="SUP-1",
            expiration_date=now + timedelta(days=15)
        )

def test_engine_operations_with_sku_instance():
    engine = LotTrackingEngine()
    now = datetime.now(timezone.utc)
    sku_vo = SKU("PROD-VO")

    lot1 = Lot(id="1", sku=sku_vo, supplier_id="SUP-1", expiration_date=now + timedelta(days=10))
    lot2 = Lot(id="2", sku="PROD-VO", supplier_id="SUP-1", expiration_date=now + timedelta(days=5))
    engine.register_lot(lot1)
    engine.register_lot(lot2)

    # Test enforce_fefo_allocation with SKU object
    allocated = engine.enforce_fefo_allocation(sku_vo, 5)
    assert len(allocated) == 2
    assert allocated[0].id == "2"
    assert allocated[1].id == "1"

    # Test trigger_supplier_recall with SKU object
    recalled = engine.trigger_supplier_recall("SUP-1", sku_vo)
    assert len(recalled) == 2

def test_lot_status_coercion_and_validation():
    now = datetime.now(timezone.utc)
    lot = Lot(
        id="lot-status",
        sku="SKU-1",
        supplier_id="SUP-1",
        expiration_date=now + timedelta(days=30),
        status="QUARANTINED"
    )
    assert isinstance(lot.status, LotStatus)
    assert lot.status == LotStatus.QUARANTINED
    assert lot.status == "QUARANTINED"

    lot_enum = Lot(
        id="lot-enum",
        sku="SKU-1",
        supplier_id="SUP-1",
        expiration_date=now + timedelta(days=30),
        status=LotStatus.RECALLED
    )
    assert lot_enum.status == LotStatus.RECALLED

    with pytest.raises(ValueError):
        Lot(
            id="lot-invalid-status",
            sku="SKU-1",
            supplier_id="SUP-1",
            expiration_date=now + timedelta(days=30),
            status="INVALID_STATUS"
        )
