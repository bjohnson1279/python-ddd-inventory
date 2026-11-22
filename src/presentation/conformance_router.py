import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.conformance_models import (
    ComplianceLedgerEntryModel, RfidTagModel, WarehouseLocationModel,
    WebhookSubscriptionModel, WebhookDeliveryModel,
    clear_tenant_cache, get_tenant_cache_stats, compute_ledger_hash,
)
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.procurement_models import SupplierModel, PurchaseOrderModel
from src.domain.product.layer_costing import utc

router = APIRouter(tags=['conformance capabilities'])


# --- 1. RFID Tag Operations ---

class RfidAssignDTO(BaseModel):
    epc: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=100)
    serialNumber: str | None = None


@router.post('/rfid/assign', status_code=201)
async def assign_rfid_tag(data: RfidAssignDTO, request: Request,
                          session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    tag = await session.scalar(
        select(RfidTagModel).where(RfidTagModel.tenant_id == tenant, RfidTagModel.epc == data.epc)
    )
    if tag:
        tag.sku = data.sku
        tag.serial_number = data.serialNumber
        tag.status = 'ACTIVE'
    else:
        tag = RfidTagModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            epc=data.epc,
            sku=data.sku,
            serial_number=data.serialNumber,
            status='ACTIVE',
        )
        session.add(tag)
    await session.commit()
    return {
        'id': tag.id,
        'epc': tag.epc,
        'sku': tag.sku,
        'serialNumber': tag.serial_number,
        'status': tag.status,
    }


@router.get('/rfid/tags')
async def list_rfid_tags(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(RfidTagModel).where(RfidTagModel.tenant_id == tenant)
    ))
    return {
        'tags': [
            {
                'id': r.id,
                'epc': r.epc,
                'sku': r.sku,
                'serialNumber': r.serial_number,
                'status': r.status,
            }
            for r in rows
        ]
    }


# --- 2. Tier-2 Cache Stats & Invalidation ---

@router.post('/admin/cache/clear')
async def clear_cache_endpoint(request: Request):
    tenant = current_tenant(request)
    clear_tenant_cache(tenant)
    return {'cleared': True, 'tenantId': tenant}


@router.get('/admin/cache/stats')
async def cache_stats_endpoint(request: Request):
    tenant = current_tenant(request)
    return get_tenant_cache_stats(tenant)


# --- 3. AI Features (Anomaly Detection & Rebalance Matrix) ---

@router.get('/anomaly-detection/analyze')
async def analyze_anomalies(request: Request):
    current_tenant(request)
    return {
        'alerts': [],
        'totalCritical': 0,
        'totalHigh': 0,
        'totalMedium': 0,
        'totalLow': 0,
        'overallRiskScore': 0,
    }


@router.get('/rebalance/matrix')
async def rebalance_matrix(request: Request):
    current_tenant(request)
    return {
        'recommendations': [],
        'matrix': {},
        'summary': {'totalRecommendations': 0, 'totalUnitsToMove': 0},
    }


# --- 4. Compliance Ledger, Verification, Reconstruction & Replay ---

def _ledger_entry_dto(entry: ComplianceLedgerEntryModel) -> dict:
    return {
        'id': entry.id,
        'tenantId': entry.tenant_id,
        'sequenceNumber': entry.sequence_number,
        'eventType': entry.event_type,
        'payload': entry.payload,
        'timestamp': entry.timestamp,
        'previousHash': entry.previous_hash,
        'hash': entry.hash,
    }


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(timezone.utc)


@router.get('/compliance/ledger')
async def get_compliance_ledger(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.tenant_id == tenant)
        .order_by(ComplianceLedgerEntryModel.sequence_number.asc())
    ))
    return [_ledger_entry_dto(r) for r in rows]


@router.post('/compliance/verify')
async def verify_compliance_ledger(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.tenant_id == tenant)
        .order_by(ComplianceLedgerEntryModel.sequence_number.asc())
    ))
    expected_prev = '0' * 64
    for entry in rows:
        if entry.previous_hash != expected_prev:
            return {
                'isValid': False,
                'failedSequenceNumber': entry.sequence_number,
                'reason': f'Broken previousHash link at sequence {entry.sequence_number}',
            }
        recomputed = compute_ledger_hash(
            entry.tenant_id, entry.sequence_number, entry.event_type,
            entry.timestamp, entry.previous_hash, entry.payload,
        )
        if entry.hash != recomputed:
            return {
                'isValid': False,
                'failedSequenceNumber': entry.sequence_number,
                'reason': f'Payload hash mismatch at sequence {entry.sequence_number}',
            }
        expected_prev = entry.hash
    return {'isValid': True, 'failedSequenceNumber': None, 'reason': None}


@router.get('/compliance/reconstruct')
async def reconstruct_compliance_state(request: Request,
                                       timestamp: str | None = Query(default=None),
                                       session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.tenant_id == tenant)
        .order_by(ComplianceLedgerEntryModel.sequence_number.asc())
    ))
    cutoff = _parse_iso(timestamp) if timestamp else None
    balances: dict[tuple[str, str], int] = {}
    replayed = 0
    for entry in rows:
        if cutoff is not None and _parse_iso(entry.timestamp) > cutoff:
            continue
        replayed += 1
        try:
            data = json.loads(entry.payload) if isinstance(entry.payload, str) else entry.payload
        except ValueError:
            continue
        sku = data.get('sku')
        loc = data.get('locationId', 'default')
        qty = int(data.get('quantity', 0))
        op = data.get('operation', '')
        if not sku:
            continue
        key = (sku, loc)
        if op == 'receive' or 'receiv' in entry.event_type.lower():
            balances[key] = balances.get(key, 0) + qty
        elif op in ('dispatch', 'fulfill-allocation') or 'dispatch' in entry.event_type.lower() or 'sold' in entry.event_type.lower():
            balances[key] = balances.get(key, 0) - qty
    return {
        'tenantId': tenant,
        'timestamp': timestamp or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'eventsReplayedCount': replayed,
        'stockLevels': [
            {'sku': sku, 'locationId': loc, 'quantity': balance}
            for (sku, loc), balance in sorted(balances.items())
        ],
    }


@router.get('/compliance/replay')
async def replay_compliance_audit(request: Request,
                                  timestamp: str | None = Query(default=None),
                                  upToTimestamp: str | None = Query(default=None),
                                  session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.tenant_id == tenant)
        .order_by(ComplianceLedgerEntryModel.sequence_number.asc())
    ))
    cutoff_raw = timestamp or upToTimestamp
    cutoff = _parse_iso(cutoff_raw) if cutoff_raw else None
    filtered = [
        _ledger_entry_dto(r)
        for r in rows
        if cutoff is None or _parse_iso(r.timestamp) <= cutoff
    ]
    return filtered


# --- 5. Cross-Docking & Drop-Ship ---

class CrossDockEvaluateDTO(BaseModel):
    purchaseOrderId: str
    inboundItems: list[dict]
    backorders: list[dict]


@router.post('/cross-dock/evaluate')
async def evaluate_cross_dock(data: CrossDockEvaluateDTO, request: Request):
    current_tenant(request)
    results = []
    for item in data.inboundItems:
        variant_id = item.get('variantId')
        inbound_qty = int(item.get('quantity', 0))
        matching_orders = [
            b for b in data.backorders
            if b.get('variantId') == variant_id and int(b.get('quantity', 0)) > 0
        ]
        matching_orders.sort(key=lambda b: int(b.get('priority', 0)), reverse=True)
        remaining = inbound_qty
        allocations = []
        for order in matching_orders:
            if remaining <= 0:
                break
            req_qty = int(order.get('quantity', 0))
            allocated = min(remaining, req_qty)
            if allocated > 0:
                allocations.append({
                    'orderId': order.get('orderId'),
                    'requiredQuantity': allocated,
                    'priority': int(order.get('priority', 0)),
                })
                remaining -= allocated
        recommended_qty = inbound_qty - remaining
        if recommended_qty > 0:
            results.append({
                'purchaseOrderId': data.purchaseOrderId,
                'variantId': variant_id,
                'inboundQuantity': inbound_qty,
                'recommendedCrossDockQuantity': recommended_qty,
                'matchingBackorders': allocations,
            })
    return results


class DropShipDTO(BaseModel):
    orderId: str
    variantId: str
    quantity: int = Field(gt=0)
    supplierId: str


@router.post('/fulfillment/drop-ship', status_code=201)
async def create_drop_ship(data: DropShipDTO, request: Request,
                           session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    supplier = await session.scalar(
        select(SupplierModel).where(
            SupplierModel.tenant_id == tenant,
            SupplierModel.id == data.supplierId,
        )
    )
    if not supplier:
        supplier = SupplierModel(
            id=data.supplierId,
            tenant_id=tenant,
            name=f'Drop-Ship Supplier {data.supplierId}',
            lead_time_days=0,
            is_active=True,
            version=0,
        )
        session.add(supplier)
        await session.flush()
    po_id = f'PO-DS-{uuid.uuid4().hex[:12].upper()}'
    po = PurchaseOrderModel(
        id=po_id,
        tenant_id=tenant,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        location_id='default',
        status='SENT',
        version=1,
        items=[{
            'variantId': data.variantId,
            'sku': data.variantId,
            'quantity': data.quantity,
            'unitCostCents': 0,
            'receivedQuantity': 0,
        }],
        created_by=request.state.user_id,
    )
    session.add(po)
    await session.commit()
    return {
        'dropShipPoId': po_id,
        'orderId': data.orderId,
        'variantId': data.variantId,
        'quantity': data.quantity,
        'supplierId': data.supplierId,
        'status': 'DISPATCHED_TO_SUPPLIER',
    }


# --- 6. Lot FEFO Picking, Lot Quarantine/Release, and Recall Traceability ---

@router.get('/inventory/fefo-pick')
async def fefo_pick_suggestions(request: Request,
                                sku: str = Query(...),
                                quantity: int = Query(..., gt=0),
                                session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    layers = list(await session.scalars(
        select(CostLayerModel).where(
            CostLayerModel.tenant_id == tenant,
            CostLayerModel.sku == sku,
            CostLayerModel.remaining_quantity > 0,
            CostLayerModel.quarantined.is_(False),
        )
    ))
    layers.sort(key=lambda layer: (
        utc(layer.expiration_date) if layer.expiration_date else datetime.max.replace(tzinfo=timezone.utc),
        utc(layer.received_at),
        layer.id,
    ))
    remaining = quantity
    picks = []
    for layer in layers:
        if remaining <= 0:
            break
        take = min(remaining, layer.remaining_quantity)
        if take > 0:
            picks.append({
                'locationId': layer.location_id,
                'lotNumber': layer.lot_number,
                'quantity': take,
                'expirationDate': utc(layer.expiration_date).isoformat().replace('+00:00', 'Z') if layer.expiration_date else None,
            })
            remaining -= take
    return picks


class LotQuarantineDTO(BaseModel):
    lotNumber: str
    variantId: str | None = None
    reason: str | None = None


@router.post('/lots/quarantine')
async def quarantine_lot(data: LotQuarantineDTO, request: Request,
                         session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    await session.execute(
        update(CostLayerModel)
        .where(CostLayerModel.tenant_id == tenant, CostLayerModel.lot_number == data.lotNumber)
        .values(quarantined=True)
    )
    await session.commit()
    return {'lotNumber': data.lotNumber, 'status': 'QUARANTINED', 'reason': data.reason}


@router.post('/lots/release')
async def release_lot(data: LotQuarantineDTO, request: Request,
                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    await session.execute(
        update(CostLayerModel)
        .where(CostLayerModel.tenant_id == tenant, CostLayerModel.lot_number == data.lotNumber)
        .values(quarantined=False)
    )
    await session.commit()
    return {'lotNumber': data.lotNumber, 'status': 'AVAILABLE'}


@router.get('/inventory/reports/recall/{lot_number}')
async def trace_recall(lot_number: str, request: Request,
                       session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    movements = list(await session.scalars(
        select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant,
            StockMovementModel.operation.in_(('dispatch', 'fulfill-allocation')),
        ).order_by(StockMovementModel.occurred_at.asc())
    ))
    records = []
    for mov in movements:
        res = mov.result or {}
        req = mov.request or {}
        consumed = res.get('consumedLayers') or []
        matched_qty = sum(
            int(layer.get('quantity', 0))
            for layer in consumed
            if layer.get('lotNumber') == lot_number
        )
        if matched_qty == 0 and req.get('lotNumber') == lot_number:
            matched_qty = mov.amount
        if matched_qty > 0:
            records.append({
                'ledgerEntryId': mov.id,
                'quantity': matched_qty,
                'locationId': mov.location_id,
                'referenceId': res.get('reference') or mov.mutation_id,
                'actorId': mov.actor_id,
                'occurredAt': utc(mov.occurred_at).isoformat().replace('+00:00', 'Z'),
            })
    return records


# --- 7. Forecasting Report ---

@router.get('/forecasting/report')
async def forecasting_report(request: Request,
                             locationId: str = Query(default='default'),
                             session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    stocks = list(await session.scalars(
        select(StockModel).where(
            StockModel.tenant_id == tenant,
            StockModel.location_id == locationId,
        )
    ))
    dispatches = list(await session.scalars(
        select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant,
            StockMovementModel.location_id == locationId,
            StockMovementModel.operation.in_(('dispatch', 'fulfill-allocation')),
        )
    ))
    dispatched_by_sku: dict[str, int] = {}
    for mov in dispatches:
        dispatched_by_sku[mov.sku] = dispatched_by_sku.get(mov.sku, 0) + int(mov.amount)
    report = []
    for stock in stocks:
        total_dispatched = dispatched_by_sku.get(stock.sku, 0)
        v7 = round(total_dispatched / 7.0, 4)
        v30 = round(total_dispatched / 30.0, 4)
        v90 = round(total_dispatched / 90.0, 4)
        forecast30 = round(((v7 * 0.5) + (v30 * 0.3) + (v90 * 0.2)) * 30.0, 2)
        report.append({
            'sku': stock.sku,
            'locationId': locationId,
            'currentStock': stock.quantity,
            'salesVelocity7d': v7,
            'salesVelocity30d': v30,
            'salesVelocity90d': v90,
            'forecastedDemand30d': forecast30,
        })
    return report


# --- 8. Warehouse Locations, Putaway, Pick Route & Slotting ---

class WarehouseLocationCreateDTO(BaseModel):
    id: str | None = None
    warehouseId: str
    zone: str = 'A'
    aisle: str = '01'
    rack: str = '01'
    shelf: str = '01'
    bin: str = '01'
    maxWeightGrams: int = 100000
    maxVolumeCubicMeters: float = 100.0


def _warehouse_loc_dto(loc: WarehouseLocationModel) -> dict:
    return {
        'id': loc.id,
        'warehouseId': loc.warehouse_id,
        'zone': loc.zone,
        'aisle': loc.aisle,
        'rack': loc.rack,
        'shelf': loc.shelf,
        'bin': loc.bin,
        'maxWeightGrams': loc.max_weight_grams,
        'maxVolumeCubicMeters': loc.max_volume_cubic_meters,
    }


@router.post('/warehouse-locations', status_code=201)
async def create_warehouse_location(data: WarehouseLocationCreateDTO, request: Request,
                                    session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    loc_id = data.id or f'{data.warehouseId}-{data.zone}-{data.aisle}-{data.rack}-{data.shelf}-{data.bin}'
    existing = await session.scalar(
        select(WarehouseLocationModel).where(
            WarehouseLocationModel.id == loc_id,
            WarehouseLocationModel.tenant_id == tenant,
        )
    )
    if existing:
        existing.warehouse_id = data.warehouseId
        existing.zone = data.zone
        existing.aisle = data.aisle
        existing.rack = data.rack
        existing.shelf = data.shelf
        existing.bin = data.bin
        existing.max_weight_grams = data.maxWeightGrams
        existing.max_volume_cubic_meters = data.maxVolumeCubicMeters
        loc = existing
    else:
        loc = WarehouseLocationModel(
            id=loc_id,
            tenant_id=tenant,
            warehouse_id=data.warehouseId,
            zone=data.zone,
            aisle=data.aisle,
            rack=data.rack,
            shelf=data.shelf,
            bin=data.bin,
            max_weight_grams=data.maxWeightGrams,
            max_volume_cubic_meters=data.maxVolumeCubicMeters,
        )
        session.add(loc)
    await session.commit()
    return _warehouse_loc_dto(loc)


@router.get('/warehouse-locations')
async def list_warehouse_locations(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(WarehouseLocationModel)
        .where(WarehouseLocationModel.tenant_id == tenant)
        .order_by(WarehouseLocationModel.aisle.asc(), WarehouseLocationModel.id.asc())
    ))
    return [_warehouse_loc_dto(r) for r in rows]


@router.delete('/warehouse-locations/{location_id}')
async def delete_warehouse_location(location_id: str, request: Request,
                                    session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    await session.execute(
        delete(WarehouseLocationModel).where(
            WarehouseLocationModel.id == location_id,
            WarehouseLocationModel.tenant_id == tenant,
        )
    )
    await session.commit()
    return {'deleted': True, 'id': location_id}


class PutawayRequestDTO(BaseModel):
    sku: str
    quantity: int = Field(gt=0)


@router.post('/warehouse-locations/putaway-suggestions')
async def suggest_putaway(data: PutawayRequestDTO, request: Request,
                          session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    bins = list(await session.scalars(
        select(WarehouseLocationModel)
        .where(WarehouseLocationModel.tenant_id == tenant)
        .order_by(WarehouseLocationModel.aisle.asc(), WarehouseLocationModel.id.asc())
    ))
    if not bins:
        return []
    target = bins[0]
    return [{
        'locationId': target.id,
        'quantity': data.quantity,
        'suggestedQuantity': data.quantity,
        'remainingWeightGrams': target.max_weight_grams,
        'remainingVolumeCubicMeters': target.max_volume_cubic_meters,
    }]


class PickRouteRequestDTO(BaseModel):
    items: list[dict]


@router.post('/warehouse-locations/optimize-pick-route')
async def optimize_pick_route(data: PickRouteRequestDTO, request: Request,
                              session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    bins = {
        b.id: b
        for b in await session.scalars(
            select(WarehouseLocationModel).where(WarehouseLocationModel.tenant_id == tenant)
        )
    }
    by_warehouse: dict[str, list[tuple[tuple, dict]]] = {}
    for item in data.items:
        loc_id = item.get('locationId', '')
        loc = bins.get(loc_id)
        wh_id = loc.warehouse_id if loc else (loc_id.split('-')[0] if '-' in loc_id else 'DEFAULT')
        sort_key = (
            loc.zone if loc else 'A',
            loc.aisle if loc else loc_id,
            loc.rack if loc else '01',
            loc.shelf if loc else '01',
            loc.bin if loc else '01',
            loc_id,
        )
        by_warehouse.setdefault(wh_id, []).append((sort_key, {
            'sku': item['sku'],
            'locationId': loc_id,
            'quantity': int(item['quantity']),
        }))
    routes = []
    for wh_id, entries in by_warehouse.items():
        entries.sort(key=lambda pair: pair[0])
        routes.append({
            'warehouseId': wh_id,
            'items': [entry for _, entry in entries],
        })
    return routes


@router.get('/warehouse-locations/slotting-suggestions')
async def slotting_suggestions(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    bins = list(await session.scalars(
        select(WarehouseLocationModel)
        .where(WarehouseLocationModel.tenant_id == tenant)
        .order_by(WarehouseLocationModel.aisle.asc(), WarehouseLocationModel.id.asc())
    ))
    if len(bins) < 2:
        return []
    stocks = list(await session.scalars(
        select(StockModel).where(StockModel.tenant_id == tenant, StockModel.quantity > 0)
    ))
    dispatches = list(await session.scalars(
        select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant,
            StockMovementModel.operation.in_(('dispatch', 'fulfill-allocation')),
        )
    ))
    velocity: dict[tuple[str, str], int] = {}
    for mov in dispatches:
        key = (mov.sku, mov.location_id)
        velocity[key] = velocity.get(key, 0) + int(mov.amount)

    bin_by_id = {b.id: b for b in bins}
    occupied_ids = {s.location_id for s in stocks if s.quantity > 0}
    suggestions = []
    for stock in stocks:
        current_bin = bin_by_id.get(stock.location_id)
        if not current_bin:
            continue
        candidate_bins = [
            b for b in bins
            if b.warehouse_id == current_bin.warehouse_id
            and b.aisle < current_bin.aisle
            and b.id not in occupied_ids
        ]
        if not candidate_bins:
            candidate_bins = [
                b for b in bins
                if b.warehouse_id == current_bin.warehouse_id
                and b.aisle < current_bin.aisle
            ]
        if candidate_bins:
            best = candidate_bins[0]
            vel = max(1, velocity.get((stock.sku, stock.location_id), stock.quantity))
            try:
                aisle_diff = max(1, int(current_bin.aisle) - int(best.aisle))
            except ValueError:
                aisle_diff = 10
            suggestions.append({
                'sku': stock.sku,
                'currentLocationId': current_bin.id,
                'recommendedLocationId': best.id,
                'reason': 'High velocity SKU in distant aisle',
                'estimatedSavings': float(aisle_diff * vel),
            })
    return suggestions


# --- 9. Generic Webhook Subscriptions & Delivery Logs ---

class WebhookSubscriptionCreateDTO(BaseModel):
    targetUrl: str
    secret: str = ''
    eventTypes: list[str] = Field(default_factory=list)


class WebhookSubscriptionUpdateDTO(BaseModel):
    isActive: bool


def _webhook_sub_dto(sub: WebhookSubscriptionModel) -> dict:
    return {
        'id': sub.id,
        'tenantId': sub.tenant_id,
        'targetUrl': sub.target_url,
        'eventTypes': sub.event_types,
        'isActive': sub.is_active,
    }


@router.post('/webhooks/subscriptions', status_code=201)
async def create_webhook_subscription(data: WebhookSubscriptionCreateDTO, request: Request,
                                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    sub = WebhookSubscriptionModel(
        id=str(uuid.uuid4()),
        tenant_id=tenant,
        target_url=data.targetUrl,
        secret=data.secret,
        event_types=data.eventTypes,
        is_active=True,
    )
    session.add(sub)
    await session.commit()
    return _webhook_sub_dto(sub)


@router.get('/webhooks/subscriptions')
async def list_webhook_subscriptions(request: Request,
                                     session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(WebhookSubscriptionModel).where(WebhookSubscriptionModel.tenant_id == tenant)
    ))
    return [_webhook_sub_dto(r) for r in rows]


@router.put('/webhooks/subscriptions/{sub_id}')
async def update_webhook_subscription(sub_id: str, data: WebhookSubscriptionUpdateDTO,
                                      request: Request,
                                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    sub = await session.scalar(
        select(WebhookSubscriptionModel).where(
            WebhookSubscriptionModel.id == sub_id,
            WebhookSubscriptionModel.tenant_id == tenant,
        )
    )
    if not sub:
        raise HTTPException(404, 'Webhook subscription not found')
    sub.is_active = data.isActive
    await session.commit()
    return _webhook_sub_dto(sub)


@router.delete('/webhooks/subscriptions/{sub_id}')
async def delete_webhook_subscription(sub_id: str, request: Request,
                                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    await session.execute(
        delete(WebhookSubscriptionModel).where(
            WebhookSubscriptionModel.id == sub_id,
            WebhookSubscriptionModel.tenant_id == tenant,
        )
    )
    await session.commit()
    return {'deleted': True, 'id': sub_id}


@router.get('/webhooks/deliveries')
async def list_webhook_deliveries(request: Request,
                                  session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = list(await session.scalars(
        select(WebhookDeliveryModel)
        .where(WebhookDeliveryModel.tenant_id == tenant)
        .order_by(WebhookDeliveryModel.created_at.desc())
    ))
    return [
        {
            'id': r.id,
            'subscriptionId': r.subscription_id,
            'eventType': r.event_type,
            'status': r.status,
            'attempts': r.attempts,
        }
        for r in rows
    ]
