from abc import ABC, abstractmethod
from typing import List, Optional
from src.domain.supplier_collaboration.entity import (
    Supplier, PurchaseOrder, AdvanceShippingNotice, SupplierPerformance
)

class SupplierCollaborationRepository(ABC):
    @abstractmethod
    async def get_supplier(self, supplier_id: str) -> Optional[Supplier]:
        pass
        
    @abstractmethod
    async def get_po(self, po_id: str) -> Optional[PurchaseOrder]:
        pass
        
    @abstractmethod
    async def save_po(self, po: PurchaseOrder) -> PurchaseOrder:
        pass
        
    @abstractmethod
    async def get_supplier_pos(self, supplier_id: str) -> List[PurchaseOrder]:
        pass
        
    @abstractmethod
    async def save_asn(self, asn: AdvanceShippingNotice) -> AdvanceShippingNotice:
        pass
        
    @abstractmethod
    async def get_performance(self, supplier_id: str) -> Optional[SupplierPerformance]:
        pass
