import pytest
from src.domain.intercompany.entity import IntercompanyTransfer, TransferStatus
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError

def test_intercompany_transfer_sku_conversion():
    transfer = IntercompanyTransfer(
        id="TR1",
        tenant_id="T1",
        source_entity_id="E1",
        destination_entity_id="E2",
        sku="SKU-123",
        quantity=10,
        transfer_price_cents=1000,
        status=TransferStatus.DRAFT,
    )
    assert isinstance(transfer.sku, SKU)
    assert transfer.sku.value == "SKU-123"

def test_intercompany_transfer_with_sku_object():
    sku_obj = SKU("SKU-999")
    transfer = IntercompanyTransfer(
        id="TR2",
        tenant_id="T1",
        source_entity_id="E1",
        destination_entity_id="E2",
        sku=sku_obj,
        quantity=5,
        transfer_price_cents=2000,
        status=TransferStatus.DRAFT,
    )
    assert transfer.sku == sku_obj

def test_intercompany_transfer_invalid_sku():
    with pytest.raises(InvalidSKUError):
        IntercompanyTransfer(
            id="TR3",
            tenant_id="T1",
            source_entity_id="E1",
            destination_entity_id="E2",
            sku="invalid_sku!",
            quantity=1,
            transfer_price_cents=500,
            status=TransferStatus.DRAFT,
        )
