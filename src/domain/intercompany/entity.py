from dataclasses import dataclass
from enum import Enum
from typing import Optional

class TransferStatus(Enum):
    DRAFT = "DRAFT"
    SHIPPED = "SHIPPED"
    RECEIVED = "RECEIVED"
    COMPLETED = "COMPLETED"

class PricingRuleType(Enum):
    COST_PLUS = "COST_PLUS"
    MARKET_BASED = "MARKET_BASED"

@dataclass
class LegalEntity:
    id: str
    tenant_id: str
    name: str
    currency_code: str
    tax_identification_number: str

@dataclass
class TransferPricingRule:
    source_entity_id: str
    destination_entity_id: str
    rule_type: PricingRuleType
    markup_percentage: float

@dataclass
class IntercompanyTransfer:
    id: str
    tenant_id: str
    source_entity_id: str
    destination_entity_id: str
    sku: str
    quantity: int
    transfer_price_cents: int
    status: TransferStatus
    tariffs_cents: int = 0

    def ship(self) -> None:
        if self.status != TransferStatus.DRAFT:
            raise ValueError("Can only ship DRAFT transfers")
        self.status = TransferStatus.SHIPPED

    def receive(self) -> None:
        if self.status != TransferStatus.SHIPPED:
            raise ValueError("Can only receive SHIPPED transfers")
        self.status = TransferStatus.RECEIVED

    def complete(self) -> None:
        if self.status != TransferStatus.RECEIVED:
            raise ValueError("Can only complete RECEIVED transfers")
        self.status = TransferStatus.COMPLETED

@dataclass
class IntercompanyJournalEntry:
    transfer_id: str
    entity_id: str
    debit_account: str
    credit_account: str
    amount_cents: int
    is_elimination: bool
