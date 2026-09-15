from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Optional
from .value_objects import SKU, Money
from src.domain.base_entity import AggregateRoot

def generate_uuid() -> str:
    return str(uuid.uuid4())

@dataclass
class Product(AggregateRoot):
    id: str = field(default_factory=generate_uuid)
    sku: SKU = field(default_factory=lambda: SKU('DUMMY-SKU'))
    name: str = ''
    description: Optional[str] = None
    price: Money = field(default_factory=lambda: Money(Decimal('0.00')))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, sku: str, name: str, description: Optional[str], price_amount: str) -> 'Product':
        from decimal import Decimal
        return cls(
            sku=SKU(sku),
            name=name,
            description=description,
            price=Money(Decimal(price_amount))
        )

    def update_price(self, new_price_amount: str):
        from decimal import Decimal
        self.price = Money(Decimal(new_price_amount))
        self.updated_at = datetime.now(timezone.utc)

