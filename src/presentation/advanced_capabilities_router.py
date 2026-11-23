from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.infrastructure.auth.identity import current_tenant
from src.domain.routing import Location, OrderLine, StockLevel, OrderRoutingEngine
from src.domain.hardware.printer import ThermalPrinterEngine, ARGuidanceEngine
from src.domain.ai_models.copilot import WarehouseDigitalTwin, ConversationalAICopilot
from src.domain.ai_models.predictive_engine import PredictiveEngine
from src.domain.analytics.esg import ESGEmissionsTracker
from src.domain.workflow import ApprovalWorkflowEngine, WorkflowExecution
from src.domain.intercompany.entity import (
    IntercompanyTransfer,
    TransferPricingRule,
    PricingRuleType,
    TransferStatus,
)
from src.domain.intercompany.services import (
    TransferPricingService,
    IntercompanyAccountingService,
)
from src.domain.billing.entity import TenantBillingTier, ApiUsageRecord, TierName
from src.domain.billing.services import BillingHookService
from src.domain.vision.entity import VisionInspection
from src.domain.vision.services import ComputerVisionService, QaGatewayService
from src.domain.yield_management.entity import (
    LiquidationProfile,
    InventoryYieldMetrics,
)
from src.domain.yield_management.services import (
    YieldCalculationService,
    DynamicPricingEngine,
)

router = APIRouter(tags=["advanced capabilities"])


# --- 4.3 Multi-Location Order Routing ---

class RoutingLocationDTO(BaseModel):
    id: str = Field(min_length=1)
    lat: float
    lon: float


class RoutingStockDTO(BaseModel):
    locationId: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    availableQty: int = Field(ge=0)


class RoutingOrderLineDTO(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class OrderRoutingRequestDTO(BaseModel):
    customerLat: float
    customerLon: float
    locations: List[RoutingLocationDTO]
    stockLevels: List[RoutingStockDTO]
    orderLines: List[RoutingOrderLineDTO]


@router.post("/routing/allocate")
async def allocate_order_routing(data: OrderRoutingRequestDTO, request: Request):
    tenant = current_tenant(request)
    locations = [Location(id=loc.id, lat=loc.lat, lon=loc.lon) for loc in data.locations]
    stock = [
        StockLevel(location_id=s.locationId, sku=s.sku, available_qty=s.availableQty)
        for s in data.stockLevels
    ]
    lines = [OrderLine(sku=l.sku, quantity=l.quantity) for l in data.orderLines]
    engine = OrderRoutingEngine(locations=locations, stock_levels=stock)
    try:
        plan = engine.route_order(data.customerLat, data.customerLon, lines)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "tenantId": tenant,
        "allocations": {
            loc_id: [{"sku": line.sku.value, "quantity": line.quantity} for line in allocs]
            for loc_id, allocs in plan.items()
        },
    }


# --- 11.3 Thermal Printing (ZPL/TSPL) & AR Guidance ---

class ThermalPrintRequestDTO(BaseModel):
    sku: str = Field(min_length=1)
    lotNumber: str = Field(min_length=1)


@router.post("/hardware/print-thermal")
async def print_thermal_label(data: ThermalPrintRequestDTO, request: Request):
    tenant = current_tenant(request)
    zpl = ThermalPrinterEngine().generate_zpl_label(data.sku, data.lotNumber)
    return {
        "tenantId": tenant,
        "sku": data.sku,
        "lotNumber": data.lotNumber,
        "zpl": zpl.strip(),
    }


@router.get("/hardware/ar-guidance/{bin_location}")
async def get_ar_guidance(bin_location: str, request: Request):
    tenant = current_tenant(request)
    coords = ARGuidanceEngine().generate_ar_overlay_coordinates(bin_location)
    return {"tenantId": tenant, "binLocation": bin_location, "overlay": coords}


# --- 11.4 Digital Twin & Conversational Copilot ---

class DigitalTwinSimulateDTO(BaseModel):
    throughput: int = Field(gt=0)


class CopilotQueryDTO(BaseModel):
    query: str = Field(min_length=1)


@router.post("/digital-twin/simulate")
async def simulate_digital_twin(data: DigitalTwinSimulateDTO, request: Request):
    tenant = current_tenant(request)
    result = WarehouseDigitalTwin().simulate_stress_test(data.throughput)
    return {"tenantId": tenant, **result}


@router.post("/copilot/query")
async def query_copilot(data: CopilotQueryDTO, request: Request):
    tenant = current_tenant(request)
    answer = ConversationalAICopilot().process_natural_language_query(data.query)
    return {"tenantId": tenant, "query": data.query, "response": answer}


# --- 11.5 ESG Carbon Footprint ---

class EsgEmissionsRequestDTO(BaseModel):
    distanceKm: float = Field(ge=0)
    weightKg: float = Field(ge=0)
    mode: str = Field(default="ROAD")


@router.post("/sustainability/emissions-report")
async def calculate_esg_emissions(data: EsgEmissionsRequestDTO, request: Request):
    tenant = current_tenant(request)
    mode = data.mode.upper()
    kg_co2 = ESGEmissionsTracker().calculate_scope_3_transport_emissions(
        distance_km=data.distanceKm,
        weight_kg=data.weightKg,
        mode=mode,
    )
    return {
        "tenantId": tenant,
        "mode": mode,
        "distanceKm": data.distanceKm,
        "weightKg": data.weightKg,
        "scope3KgCo2": round(kg_co2, 4),
    }


# --- 16.1 Intercompany & Multi-Entity Accounting ---

class IntercompanyPriceDTO(BaseModel):
    sourceEntityId: str = Field(min_length=1)
    destinationEntityId: str = Field(min_length=1)
    baseUnitCostCents: int = Field(ge=0)
    ruleType: str = Field(default="COST_PLUS")
    markupPercentage: float = Field(default=10.0, ge=0)


class IntercompanyJournalDTO(BaseModel):
    transferId: Optional[str] = None
    sourceEntityId: str = Field(min_length=1)
    destinationEntityId: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unitCostCents: int = Field(ge=0)
    transferPriceCents: int = Field(ge=0)


@router.post("/intercompany/transfer-price")
async def calculate_intercompany_price(data: IntercompanyPriceDTO, request: Request):
    tenant = current_tenant(request)
    try:
        rule_enum = PricingRuleType(data.ruleType.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid ruleType: {data.ruleType}") from exc
    rule = TransferPricingRule(
        source_entity_id=data.sourceEntityId,
        destination_entity_id=data.destinationEntityId,
        rule_type=rule_enum,
        markup_percentage=data.markupPercentage,
    )
    transfer_price = TransferPricingService().calculate_transfer_price(data.baseUnitCostCents, rule)
    return {
        "tenantId": tenant,
        "baseUnitCostCents": data.baseUnitCostCents,
        "transferPriceCents": transfer_price,
        "ruleType": rule_enum.value,
    }


@router.post("/intercompany/journal-entries")
async def generate_intercompany_journals(data: IntercompanyJournalDTO, request: Request):
    tenant = current_tenant(request)
    transfer = IntercompanyTransfer(
        id=data.transferId or str(uuid.uuid4()),
        tenant_id=tenant,
        source_entity_id=data.sourceEntityId,
        destination_entity_id=data.destinationEntityId,
        sku=data.sku,
        quantity=data.quantity,
        transfer_price_cents=data.transferPriceCents,
        status=TransferStatus.SHIPPED,
    )
    svc = IntercompanyAccountingService()
    shipment_entries = svc.generate_entries_for_shipment(transfer, data.unitCostCents)
    receipt_entries = svc.generate_entries_for_receipt(transfer)

    def _serialize(entries):
        return [
            {
                "transferId": e.transfer_id,
                "entityId": e.entity_id,
                "debitAccount": e.debit_account,
                "creditAccount": e.credit_account,
                "amountCents": e.amount_cents,
                "isElimination": e.is_elimination,
            }
            for e in entries
        ]

    return {
        "tenantId": tenant,
        "transferId": transfer.id,
        "shipmentEntries": _serialize(shipment_entries),
        "receiptEntries": _serialize(receipt_entries),
    }


# --- 16.2 API Metering & Billing ---

class BillingOveragesDTO(BaseModel):
    tierName: str = Field(default="PRO")
    billingCycleId: str = Field(default="current")
    baseMonthlyPriceCents: int = Field(default=0, ge=0)
    maxRequestsPerMinute: int = Field(gt=0)
    includedApiRequestsPerMonth: int = Field(ge=0)
    maxActiveSkus: int = Field(ge=0)
    apiRequestsCount: int = Field(ge=0)
    activeSkusCount: int = Field(ge=0)
    storageBytesUsed: int = Field(default=0, ge=0)


@router.post("/billing/evaluate-overages")
async def evaluate_billing_overages(data: BillingOveragesDTO, request: Request):
    tenant = current_tenant(request)
    try:
        tier_enum = TierName(data.tierName.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid tierName: {data.tierName}") from exc
    tier = TenantBillingTier(
        tier_name=tier_enum,
        max_requests_per_minute=data.maxRequestsPerMinute,
        max_active_skus=data.maxActiveSkus,
        included_api_requests_per_month=data.includedApiRequestsPerMonth,
        base_monthly_price_cents=data.baseMonthlyPriceCents,
    )
    record = ApiUsageRecord(
        tenant_id=tenant,
        billing_cycle_id=data.billingCycleId,
        api_requests_count=data.apiRequestsCount,
        storage_bytes_used=data.storageBytesUsed,
        active_skus_count=data.activeSkusCount,
    )
    events = BillingHookService().evaluate_overages(record, tier)
    return {
        "tenantId": tenant,
        "events": [
            {
                "eventId": ev.event_id,
                "eventType": ev.event_type.value,
                "quantity": ev.quantity,
                "occurredAt": ev.occurred_at.isoformat(),
            }
            for ev in events
        ],
    }


# --- 17.1 Computer Vision Receiving & QA Gateway ---

class VisionAnalyzeDTO(BaseModel):
    dockStationId: str = Field(min_length=1)
    imageUrl: str = Field(min_length=1)
    damageThreshold: float = Field(default=0.70, ge=0.0, le=1.0)


@router.post("/cv/analyze")
async def analyze_vision_inspection(data: VisionAnalyzeDTO, request: Request):
    tenant = current_tenant(request)
    inspection_id = str(uuid.uuid4())
    inspection = VisionInspection(
        inspection_id=inspection_id,
        tenant_id=tenant,
        dock_station_id=data.dockStationId,
        captured_at=datetime.now(timezone.utc),
        image_url=data.imageUrl,
    )
    result = ComputerVisionService().analyze_image(data.imageUrl, inspection_id)
    QaGatewayService().process_inspection(inspection, result, damage_threshold=data.damageThreshold)
    return {
        "tenantId": tenant,
        "inspectionId": inspection_id,
        "status": inspection.status.value,
        "detectedBarcode": result.detected_barcode,
        "damageScore": result.damage_score,
        "anomaliesDetected": result.anomalies_detected,
        "dimensions": {
            "length": result.dimensions.length,
            "width": result.dimensions.width,
            "height": result.dimensions.height,
            "unit": result.dimensions.unit.value,
        },
    }


# --- 18.1 Dynamic Pricing & Liquidation Engine ---

class YieldMarkdownRequestDTO(BaseModel):
    sku: str = Field(min_length=1)
    basePriceCents: int = Field(gt=0)
    holdingCostPerDayCents: int = Field(ge=0)
    minFloorPriceCents: int = Field(ge=0)
    daysInInventory: int = Field(ge=0)
    daysUntilExpiration: Optional[int] = Field(default=None, ge=0)
    historicalDailyDemand: float = Field(ge=0)
    currentStockQuantity: int = Field(ge=0)


@router.post("/yield/recommend-markdown")
async def recommend_yield_markdown(data: YieldMarkdownRequestDTO, request: Request):
    tenant = current_tenant(request)
    profile = LiquidationProfile(
        sku=data.sku,
        base_price_cents=data.basePriceCents,
        holding_cost_per_day_cents=data.holdingCostPerDayCents,
        min_floor_price_cents=data.minFloorPriceCents,
    )
    metrics = InventoryYieldMetrics(
        sku=data.sku,
        days_in_inventory=data.daysInInventory,
        days_until_expiration=data.daysUntilExpiration,
        historical_daily_demand=data.historicalDailyDemand,
        current_stock_quantity=data.currentStockQuantity,
    )
    engine = DynamicPricingEngine(YieldCalculationService())
    rec = engine.generate_markdown(metrics, profile)
    if rec is None:
        return {"tenantId": tenant, "sku": data.sku, "recommendation": None}
    return {
        "tenantId": tenant,
        "sku": rec.sku.value,
        "recommendation": {
            "id": rec.id,
            "recommendedPriceCents": rec.recommended_price_cents,
            "reasoning": rec.reasoning,
            "status": rec.status.value,
        },
    }


# --- 5.3 Demand Forecasting, 6.3 Slotting & 9.1 Predictive Anomaly Detection ---

class ForecastingRopDTO(BaseModel):
    sku: str = Field(min_length=1)
    historicalSales: List[int] = Field(default_factory=list)
    leadTimeDays: int = Field(ge=0)


class ForecastingSlottingDTO(BaseModel):
    warehouseMap: Dict[str, Any] = Field(default_factory=dict)
    skuVelocity: Dict[str, float] = Field(default_factory=dict)


class ForecastingTransactionDTO(BaseModel):
    sku: str = Field(min_length=1)
    quantityAdjustment: int
    timestamp: Optional[str] = None


class ForecastingAnomaliesDTO(BaseModel):
    recentTransactions: List[ForecastingTransactionDTO] = Field(default_factory=list)


@router.post("/forecasting/rop")
async def calculate_forecasting_rop(data: ForecastingRopDTO, request: Request):
    tenant = current_tenant(request)
    rop = PredictiveEngine().calculate_dynamic_rop(
        sku=data.sku,
        historical_sales=data.historicalSales,
        lead_time_days=data.leadTimeDays,
    )
    return {"tenantId": tenant, "sku": data.sku, "reorderPoint": rop}


@router.post("/forecasting/slotting")
async def optimize_forecasting_slotting(data: ForecastingSlottingDTO, request: Request):
    tenant = current_tenant(request)
    recs = PredictiveEngine().optimize_slotting(data.warehouseMap, data.skuVelocity)
    return {"tenantId": tenant, "recommendations": recs}


@router.post("/forecasting/anomalies")
async def detect_forecasting_anomalies(data: ForecastingAnomaliesDTO, request: Request):
    tenant = current_tenant(request)
    txns = []
    for item in data.recentTransactions:
        row: Dict[str, Any] = {"sku": item.sku, "quantity_adjustment": item.quantityAdjustment}
        if item.timestamp:
            try:
                row["timestamp"] = datetime.fromisoformat(item.timestamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid timestamp: {item.timestamp}") from exc
        txns.append(row)
    flagged = PredictiveEngine().detect_anomalies(txns)
    return {
        "tenantId": tenant,
        "anomalies": [
            {
                "sku": f["sku"],
                "quantityAdjustment": f["quantity_adjustment"],
                "timestamp": f["timestamp"].isoformat() if "timestamp" in f else None,
            }
            for f in flagged
        ],
    }


# --- 12.2 Configurable Approval Workflows ---

_TENANT_WORKFLOW_ENGINES: Dict[str, ApprovalWorkflowEngine] = {}
_TENANT_WORKFLOW_EXECUTIONS: Dict[str, Dict[str, WorkflowExecution]] = {}


def _workflow_engine_for(tenant_id: str) -> ApprovalWorkflowEngine:
    if tenant_id not in _TENANT_WORKFLOW_ENGINES:
        _TENANT_WORKFLOW_ENGINES[tenant_id] = ApprovalWorkflowEngine()
    return _TENANT_WORKFLOW_ENGINES[tenant_id]


def _serialize_execution(tenant_id: str, execution: WorkflowExecution) -> Dict[str, Any]:
    return {
        "tenantId": tenant_id,
        "executionId": execution.execution_id,
        "triggerEvent": execution.trigger_event,
        "status": execution.status.value,
        "payload": execution.payload,
        "steps": [
            {
                "role": s.role,
                "status": s.status.value,
                "approvedBy": s.approved_by,
                "approvedAt": s.approved_at.isoformat() if s.approved_at else None,
                "comments": s.comments,
            }
            for s in execution.steps
        ],
    }


class WorkflowTemplateDTO(BaseModel):
    eventName: str = Field(min_length=1)
    requiredRoles: List[str] = Field(min_length=1)


class WorkflowStartDTO(BaseModel):
    eventName: str = Field(min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkflowDecisionDTO(BaseModel):
    role: str = Field(min_length=1)
    userId: str = Field(min_length=1)
    comments: str = ""


@router.post("/workflows/templates", status_code=201)
async def register_workflow_template(data: WorkflowTemplateDTO, request: Request):
    tenant = current_tenant(request)
    engine = _workflow_engine_for(tenant)
    engine.register_template(data.eventName, data.requiredRoles)
    return {"tenantId": tenant, "eventName": data.eventName, "requiredRoles": data.requiredRoles}


@router.post("/workflows/start", status_code=201)
async def start_approval_workflow(data: WorkflowStartDTO, request: Request):
    tenant = current_tenant(request)
    engine = _workflow_engine_for(tenant)
    execution = engine.start_workflow(data.eventName, data.payload)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"No workflow template registered for {data.eventName}")
    _TENANT_WORKFLOW_EXECUTIONS.setdefault(tenant, {})[execution.execution_id] = execution
    return _serialize_execution(tenant, execution)


@router.get("/workflows/{execution_id}")
async def get_approval_workflow(execution_id: str, request: Request):
    tenant = current_tenant(request)
    execution = _TENANT_WORKFLOW_EXECUTIONS.get(tenant, {}).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Workflow execution not found")
    return _serialize_execution(tenant, execution)


@router.post("/workflows/{execution_id}/approve")
async def approve_workflow_step(execution_id: str, data: WorkflowDecisionDTO, request: Request):
    tenant = current_tenant(request)
    execution = _TENANT_WORKFLOW_EXECUTIONS.get(tenant, {}).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Workflow execution not found")
    if not execution.approve_step(data.role, data.userId, data.comments):
        raise HTTPException(status_code=409, detail=f"No pending step for role {data.role}")
    return _serialize_execution(tenant, execution)


@router.post("/workflows/{execution_id}/reject")
async def reject_workflow_step(execution_id: str, data: WorkflowDecisionDTO, request: Request):
    tenant = current_tenant(request)
    execution = _TENANT_WORKFLOW_EXECUTIONS.get(tenant, {}).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Workflow execution not found")
    if not execution.reject_step(data.role, data.userId, data.comments):
        raise HTTPException(status_code=409, detail=f"No pending step for role {data.role}")
    return _serialize_execution(tenant, execution)
