import pytest
from src.domain.intercompany.entity import (
    IntercompanyTransfer,
    TransferStatus,
    LegalEntity,
    TransferPricingRule,
    PricingRuleType,
    IntercompanyJournalEntry,
)
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

def test_intercompany_transfer_lifecycle_success():
    transfer = IntercompanyTransfer(
        id="TR4",
        tenant_id="T1",
        source_entity_id="E1",
        destination_entity_id="E2",
        sku="SKU-VALID",
        quantity=5,
        transfer_price_cents=1000,
        status=TransferStatus.DRAFT,
    )
    assert transfer.status == TransferStatus.DRAFT
    assert transfer.tariffs_cents == 0

    transfer.ship()
    assert transfer.status == TransferStatus.SHIPPED

    transfer.receive()
    assert transfer.status == TransferStatus.RECEIVED

    transfer.complete()
    assert transfer.status == TransferStatus.COMPLETED

def test_intercompany_transfer_invalid_ship_transitions():
    for invalid_status in (TransferStatus.SHIPPED, TransferStatus.RECEIVED, TransferStatus.COMPLETED):
        transfer = IntercompanyTransfer(
            id="TR5",
            tenant_id="T1",
            source_entity_id="E1",
            destination_entity_id="E2",
            sku="SKU-VALID",
            quantity=5,
            transfer_price_cents=1000,
            status=invalid_status,
        )
        with pytest.raises(ValueError, match="Can only ship DRAFT transfers"):
            transfer.ship()

def test_intercompany_transfer_invalid_receive_transitions():
    for invalid_status in (TransferStatus.DRAFT, TransferStatus.RECEIVED, TransferStatus.COMPLETED):
        transfer = IntercompanyTransfer(
            id="TR6",
            tenant_id="T1",
            source_entity_id="E1",
            destination_entity_id="E2",
            sku="SKU-VALID",
            quantity=5,
            transfer_price_cents=1000,
            status=invalid_status,
        )
        with pytest.raises(ValueError, match="Can only receive SHIPPED transfers"):
            transfer.receive()

def test_intercompany_transfer_invalid_complete_transitions():
    for invalid_status in (TransferStatus.DRAFT, TransferStatus.SHIPPED, TransferStatus.COMPLETED):
        transfer = IntercompanyTransfer(
            id="TR7",
            tenant_id="T1",
            source_entity_id="E1",
            destination_entity_id="E2",
            sku="SKU-VALID",
            quantity=5,
            transfer_price_cents=1000,
            status=invalid_status,
        )
        with pytest.raises(ValueError, match="Can only complete RECEIVED transfers"):
            transfer.complete()

def test_legal_entity_creation():
    entity = LegalEntity(
        id="LE-1",
        tenant_id="TENANT-1",
        name="Global Corp US",
        currency_code="USD",
        tax_identification_number="US-123456789",
    )
    assert entity.id == "LE-1"
    assert entity.tenant_id == "TENANT-1"
    assert entity.name == "Global Corp US"
    assert entity.currency_code == "USD"
    assert entity.tax_identification_number == "US-123456789"

def test_transfer_pricing_rule_creation():
    rule = TransferPricingRule(
        source_entity_id="E1",
        destination_entity_id="E2",
        rule_type=PricingRuleType.COST_PLUS,
        markup_percentage=15.5,
    )
    assert rule.source_entity_id == "E1"
    assert rule.destination_entity_id == "E2"
    assert rule.rule_type == PricingRuleType.COST_PLUS
    assert rule.markup_percentage == 15.5

def test_intercompany_journal_entry_creation():
    entry = IntercompanyJournalEntry(
        transfer_id="TR-100",
        entity_id="E1",
        debit_account="1200-INTERCOMPANY-AR",
        credit_account="4000-INTERCOMPANY-REVENUE",
        amount_cents=5000,
        is_elimination=False,
    )
    assert entry.transfer_id == "TR-100"
    assert entry.entity_id == "E1"
    assert entry.debit_account == "1200-INTERCOMPANY-AR"
    assert entry.credit_account == "4000-INTERCOMPANY-REVENUE"
    assert entry.amount_cents == 5000
    assert entry.is_elimination is False
