from dataclasses import dataclass
from typing import List, Dict

@dataclass
class InboundASN:
    asn_id: str
    supplier_id: str
    sku: str
    quantity: int
    destination_dock: str

@dataclass
class OutboundOrder:
    order_id: str
    sku: str
    quantity: int
    customer_id: str

class CrossDockingEngine:
    """Dynamic cross-docking and supplier fulfillment routing logic."""
    
    def __init__(self):
        # Queues pending cross-dock assignments
        self.pending_outbound: List[OutboundOrder] = []

    def register_outbound_order(self, order: OutboundOrder):
        """Registers an order waiting for inbound stock to bypass put-away."""
        self.pending_outbound.append(order)

    def process_inbound_asn(self, asn: InboundASN) -> Dict[str, int]:
        """
        Receives an Advanced Shipping Notice (ASN) and determines if the stock
        can be immediately routed to outbound dispatch bays (cross-docking).
        Returns a mapping of order_id to fulfilled quantity.
        """
        cross_dock_assignments = {}
        remaining_qty = asn.quantity
        
        # Sort or filter pending orders by priority/date (simplified here)
        for order in list(self.pending_outbound):
            if remaining_qty <= 0:
                break
                
            if order.sku == asn.sku:
                allocated = min(remaining_qty, order.quantity)
                cross_dock_assignments[order.order_id] = allocated
                order.quantity -= allocated
                remaining_qty -= allocated
                
                if order.quantity == 0:
                    self.pending_outbound.remove(order)
                    
        # If remaining_qty > 0, it means it must go through standard put-away to bins.
        # Otherwise, the entire ASN was cross-docked.
        
        return cross_dock_assignments
