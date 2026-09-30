from abc import ABC, abstractmethod
from typing import List
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class InventoryBatch:
    batch_id: str
    quantity: int
    unit_cost: Decimal
    received_at: str

class CostingStrategy(ABC):
    @abstractmethod
    def calculate_cost_of_goods_sold(self, quantity_sold: int, batches: List[InventoryBatch]) -> Decimal:
        """Calculate COGS based on the strategy pattern."""
        pass

class FIFOCosting(CostingStrategy):
    """First-In, First-Out costing strategy."""
    def calculate_cost_of_goods_sold(self, quantity_sold: int, batches: List[InventoryBatch]) -> Decimal:
        # Sort batches by oldest first
        sorted_batches = sorted(batches, key=lambda b: b.received_at)
        cost = Decimal('0.00')
        remaining = quantity_sold
        
        for batch in sorted_batches:
            if remaining <= 0:
                break
            taken = min(remaining, batch.quantity)
            cost += taken * batch.unit_cost
            remaining -= taken
            
        if remaining > 0:
            raise ValueError("Not enough inventory to satisfy the sale quantity.")
            
        return cost

class LIFOCosting(CostingStrategy):
    """Last-In, First-Out costing strategy."""
    def calculate_cost_of_goods_sold(self, quantity_sold: int, batches: List[InventoryBatch]) -> Decimal:
        # Sort batches by newest first
        sorted_batches = sorted(batches, key=lambda b: b.received_at, reverse=True)
        cost = Decimal('0.00')
        remaining = quantity_sold
        
        for batch in sorted_batches:
            if remaining <= 0:
                break
            taken = min(remaining, batch.quantity)
            cost += taken * batch.unit_cost
            remaining -= taken
            
        if remaining > 0:
            raise ValueError("Not enough inventory to satisfy the sale quantity.")
            
        return cost

class WACCosting(CostingStrategy):
    """Weighted Average Cost costing strategy."""
    def calculate_cost_of_goods_sold(self, quantity_sold: int, batches: List[InventoryBatch]) -> Decimal:
        # Bolt Optimization: Avoid multiple O(N) passes by calculating total quantity and value in a single pass.
        # Impact: Reduces iteration overhead and speeds up weighted average cost calculations for large batch lists.
        total_quantity = 0
        total_value = Decimal('0.00')
        for b in batches:
            total_quantity += b.quantity
            total_value += b.quantity * b.unit_cost

        if quantity_sold > total_quantity:
            raise ValueError("Not enough inventory to satisfy the sale quantity.")
            
        if total_quantity == 0:
            return Decimal('0.00')
            
        average_unit_cost = total_value / total_quantity
        return average_unit_cost * quantity_sold
