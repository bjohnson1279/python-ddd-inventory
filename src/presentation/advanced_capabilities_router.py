from datetime import datetime, timezone
import uuid
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.infrastructure.auth.identity import current_tenant
from src.domain.routing import Location, OrderLine, StockLevel, OrderRoutingEngine
from src.domain.hardware.printer import ThermalPrinterEngine, ARGuidanceEngine
from src.domain.ai_models.copilot import WarehouseDigitalTwin, ConversationalAICopilot
from src.domain.analytics.esg import ESGEmissionsTracker
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
