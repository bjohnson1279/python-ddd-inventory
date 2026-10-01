from datetime import datetime
from src.domain.supplier_collaboration.entity import AdvanceShippingNotice, PurchaseOrder

class ASNSubmissionService:
    def validate_and_submit_asn(self, po: PurchaseOrder, asn: AdvanceShippingNotice) -> AdvanceShippingNotice:
        if po.status not in ['ISSUED', 'ACKNOWLEDGED']:
            raise ValueError(f"Cannot submit ASN for PO in status {po.status}")
            
        if po.supplier_id != asn.supplier_id:
            raise ValueError("ASN supplier does not match PO supplier")
            
        if not asn.items:
            raise ValueError("ASN must contain at least one line item")
            
        # Set PO status to shipped
        po.status = 'SHIPPED'
        
        # In a full implementation, we'd emit an ASNSubmittedEvent 
        # to trigger the creation of ExpectedReceipts in the Warehouse context.
        return asn

class OTIFCalculationService:
    def calculate_otif(self, asn: AdvanceShippingNotice, actual_receipt_date: datetime, actual_quantities: dict) -> dict:
        """
        Calculates OTIF (On-Time In-Full) for a received ASN.
        actual_quantities maps sku -> received_quantity.
        """
        # On-Time
        is_on_time = actual_receipt_date <= asn.estimated_delivery_date
        
        # In-Full
        is_in_full = True
        defect_count = 0
        total_items = len(asn.items)
        
        for item in asn.items:
            received = actual_quantities.get(item.sku, 0)
            if received < item.shipped_quantity:
                is_in_full = False
                defect_count += 1
                
        defect_rate = (defect_count / total_items) * 100.0 if total_items > 0 else 0.0
        
        return {
            "on_time": is_on_time,
            "in_full": is_in_full,
            "otif_success": is_on_time and is_in_full,
            "defect_rate_percentage": defect_rate
        }
