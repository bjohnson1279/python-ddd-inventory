from typing import List
from .entity import (
    IntercompanyTransfer, TransferPricingRule, PricingRuleType, 
    IntercompanyJournalEntry, TransferStatus
)

class TransferPricingService:
    def calculate_transfer_price(
        self,
        base_unit_cost_cents: int,
        rule: TransferPricingRule
    ) -> int:
        if rule.rule_type == PricingRuleType.COST_PLUS:
            markup = base_unit_cost_cents * (rule.markup_percentage / 100.0)
            return int(base_unit_cost_cents + markup)
        elif rule.rule_type == PricingRuleType.MARKET_BASED:
            # Simplified: assuming markup represents market adjustment
            return int(base_unit_cost_cents * (1 + rule.markup_percentage / 100.0))
        return base_unit_cost_cents

class IntercompanyAccountingService:
    def generate_entries_for_shipment(
        self, 
        transfer: IntercompanyTransfer, 
        unit_cost_cents: int
    ) -> List[IntercompanyJournalEntry]:
        total_cost = transfer.quantity * unit_cost_cents
        total_revenue = transfer.quantity * transfer.transfer_price_cents
        
        entries = []
        
        # Source Entity books Intercompany AR and Intercompany Revenue
        entries.append(IntercompanyJournalEntry(
            transfer_id=transfer.id,
            entity_id=transfer.source_entity_id,
            debit_account="1200-INTERCOMPANY-AR",
            credit_account="4000-INTERCOMPANY-REVENUE",
            amount_cents=total_revenue,
            is_elimination=False
        ))
        
        # Source Entity books COGS and reduces Inventory
        entries.append(IntercompanyJournalEntry(
            transfer_id=transfer.id,
            entity_id=transfer.source_entity_id,
            debit_account="5000-COGS",
            credit_account="1400-INVENTORY",
            amount_cents=total_cost,
            is_elimination=False
        ))
        
        # Consolidation Elimination Entries (recorded at the parent/tenant level, we assign to source)
        # Eliminate Intercompany Revenue against COGS, leaving only true cost to the enterprise
        entries.append(IntercompanyJournalEntry(
            transfer_id=transfer.id,
            entity_id=transfer.source_entity_id,
            debit_account="4000-INTERCOMPANY-REVENUE",
            credit_account="5000-COGS",
            amount_cents=total_revenue,
            is_elimination=True
        ))
        
        return entries

    def generate_entries_for_receipt(
        self, 
        transfer: IntercompanyTransfer
    ) -> List[IntercompanyJournalEntry]:
        total_cost = transfer.quantity * transfer.transfer_price_cents
        entries = []
        
        # Destination Entity books Inventory and Intercompany AP
        entries.append(IntercompanyJournalEntry(
            transfer_id=transfer.id,
            entity_id=transfer.destination_entity_id,
            debit_account="1400-INVENTORY",
            credit_account="2200-INTERCOMPANY-AP",
            amount_cents=total_cost,
            is_elimination=False
        ))
        
        # Eliminate Intercompany AP against AR
        entries.append(IntercompanyJournalEntry(
            transfer_id=transfer.id,
            entity_id=transfer.destination_entity_id,
            debit_account="2200-INTERCOMPANY-AP",
            credit_account="1200-INTERCOMPANY-AR",
            amount_cents=total_cost,
            is_elimination=True
        ))
        
        # If there are tariffs, book them as an expense or capitalized inventory cost for the destination
        if transfer.tariffs_cents > 0:
            entries.append(IntercompanyJournalEntry(
                transfer_id=transfer.id,
                entity_id=transfer.destination_entity_id,
                debit_account="5100-DUTIES-AND-TARIFFS",
                credit_account="2000-ACCOUNTS-PAYABLE",
                amount_cents=transfer.tariffs_cents,
                is_elimination=False
            ))

        return entries

class ConsolidationReportService:
    def generate_consolidated_ledger(
        self,
        tenant_id: str,
        entries: List[IntercompanyJournalEntry]
    ) -> int:
        """
        Calculates consolidated net revenue by explicitly backing out elimination entries.
        """
        net_revenue = 0
        for entry in entries:
            # If not eliminated and it's a credit to Revenue
            if entry.credit_account == "4000-INTERCOMPANY-REVENUE" and not entry.is_elimination:
                net_revenue += entry.amount_cents
            # If it's an elimination debit to Revenue, it reduces the consolidated net revenue
            if entry.debit_account == "4000-INTERCOMPANY-REVENUE" and entry.is_elimination:
                net_revenue -= entry.amount_cents
                
        return net_revenue
