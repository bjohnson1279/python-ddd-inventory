from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from src.domain.supplier_collaboration.entity import AdvanceShippingNotice, ASNLineItem
from src.domain.supplier_collaboration.services import ASNSubmissionService
from src.domain.supplier_collaboration.repository import SupplierCollaborationRepository
from src.infrastructure.auth.rbac import requires_roles

router = APIRouter(prefix="/api/supplier-portal", tags=["SupplierPortal"])

# In a real setup, we'd have a DB dependency to inject the repository.
# For scaffolding/parity, we will use a mock dependency or fetch from request state.

class POAcknowledgementDTO(BaseModel):
    expected_delivery_date: datetime

class ASNLineItemDTO(BaseModel):
    sku: str
    shipped_quantity: int
    lot_number: Optional[str] = None

class ASNSubmitDTO(BaseModel):
    po_id: str
    tracking_number: str
    estimated_delivery_date: datetime
    items: List[ASNLineItemDTO]

def get_supplier_repo() -> SupplierCollaborationRepository:
    # Dependency stub
    return None

@router.get("/pos")
@requires_roles(["SUPPLIER_USER"])
async def list_supplier_pos(request: Request, repo: SupplierCollaborationRepository = Depends(get_supplier_repo)):
    supplier_id = getattr(request.state, "supplier_id", "mock_supplier_1")
    if repo:
        pos = await repo.get_supplier_pos(supplier_id)
        return {"pos": pos}
    return {"pos": []}

@router.post("/pos/{po_id}/acknowledge")
@requires_roles(["SUPPLIER_USER"])
async def acknowledge_po(po_id: str, dto: POAcknowledgementDTO, request: Request, repo: SupplierCollaborationRepository = Depends(get_supplier_repo)):
    supplier_id = getattr(request.state, "supplier_id", "mock_supplier_1")
    
    if repo:
        po = await repo.get_po(po_id)
        if not po or po.supplier_id != supplier_id:
            raise HTTPException(status_code=404, detail="PO not found")
        
        po.status = "ACKNOWLEDGED"
        po.expected_delivery_date = dto.expected_delivery_date
        po.acknowledged_date = datetime.utcnow()
        await repo.save_po(po)
        
    return {"success": True, "status": "ACKNOWLEDGED"}

@router.post("/asns")
@requires_roles(["SUPPLIER_USER"])
async def submit_asn(dto: ASNSubmitDTO, request: Request, repo: SupplierCollaborationRepository = Depends(get_supplier_repo)):
    supplier_id = getattr(request.state, "supplier_id", "mock_supplier_1")
    service = ASNSubmissionService()
    
    if repo:
        po = await repo.get_po(dto.po_id)
        if not po or po.supplier_id != supplier_id:
            raise HTTPException(status_code=404, detail="PO not found")
            
        asn_items = [
            ASNLineItem(sku=i.sku, shipped_quantity=i.shipped_quantity, lot_number=i.lot_number)
            for i in dto.items
        ]
        
        asn = AdvanceShippingNotice(
            po_id=dto.po_id,
            supplier_id=supplier_id,
            tracking_number=dto.tracking_number,
            estimated_delivery_date=dto.estimated_delivery_date,
            items=asn_items
        )
        
        try:
            valid_asn = service.validate_and_submit_asn(po, asn)
            await repo.save_asn(valid_asn)
            await repo.save_po(po) # Saves SHIPPED status
            return {"success": True, "asn_id": valid_asn.id}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
            
    return {"success": True, "asn_id": "mock_asn_id"}

@router.get("/performance")
@requires_roles(["SUPPLIER_USER"])
async def get_supplier_performance(request: Request, repo: SupplierCollaborationRepository = Depends(get_supplier_repo)):
    supplier_id = getattr(request.state, "supplier_id", "mock_supplier_1")
    if repo:
        perf = await repo.get_performance(supplier_id)
        return {"performance": perf}
    return {"performance": {"otif_percentage": 98.5, "defect_rate_percentage": 1.2}}
