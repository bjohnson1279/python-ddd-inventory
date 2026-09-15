from dataclasses import dataclass
from decimal import Decimal
from .exceptions import InvalidSKUError, InvalidPriceError
import re

@dataclass(frozen=True)
class SKU:
    value: str

    def __post_init__(self):
        if not self.value or not isinstance(self.value, str):
            raise InvalidSKUError('SKU must be a non-empty string')
        if not re.match(r'^[A-Z0-9-]+$', self.value):
            raise InvalidSKUError('SKU must contain only uppercase letters, numbers, and hyphens')

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = 'USD'

    def __post_init__(self):
        if self.amount < Decimal('0.00'):
            raise InvalidPriceError('Price cannot be negative')
        if len(self.currency) != 3:
            raise InvalidPriceError('Currency must be a 3-letter ISO code')

