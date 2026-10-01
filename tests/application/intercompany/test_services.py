import pytest
from src.domain.intercompany.entity import (
    IntercompanyTransfer, TransferPricingRule, PricingRuleType, TransferStatus
)
from src.domain.intercompany.services import (
    TransferPricingService, IntercompanyAccountingService, ConsolidationReportService
)

def test_transfer_pricing_service():
    service = TransferPricingService()
    rule = TransferPricingRule("E1", "E2", PricingRuleType.COST_PLUS, 10.0)
    
    price = service.calculate_transfer_price(1000, rule)
    assert price == 1100

def test_intercompany_accounting_lifecycle():
    acct_service = IntercompanyAccountingService()
    consolidation_service = ConsolidationReportService()
    
    transfer = IntercompanyTransfer(
        id="TR1",
        tenant_id="T1",
        source_entity_id="E1",
        destination_entity_id="E2",
        sku="SKU1",
        quantity=10,
        transfer_price_cents=1100,
        status=TransferStatus.DRAFT,
        tariffs_cents=500
    )
    
    transfer.ship()
    shipment_entries = acct_service.generate_entries_for_shipment(transfer, 1000)
    assert len(shipment_entries) == 3
    
    # Check elimination of revenue
    revenue_elim = next(e for e in shipment_entries if e.is_elimination and e.debit_account == "4000-INTERCOMPANY-REVENUE")
    assert revenue_elim.amount_cents == 11000

    transfer.receive()
    receipt_entries = acct_service.generate_entries_for_receipt(transfer)
    assert len(receipt_entries) == 3
    
    # Tariffs check
    tariff_entry = next(e for e in receipt_entries if e.debit_account == "5100-DUTIES-AND-TARIFFS")
    assert tariff_entry.amount_cents == 500
    
    # Consolidation report should net to 0 for intercompany revenue
    net_rev = consolidation_service.generate_consolidated_ledger("T1", shipment_entries + receipt_entries)
    assert net_rev == 0
