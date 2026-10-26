import copy
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.application.costing_service import locked_config, load_layers
from src.application.stock_service import StockConflict, stock_dto
from src.application.quarantine_service import record_quarantine_losses
from src.domain.product.layer_costing import consume, redistribute_value, eligible_layers, utc, validate_total_value
from src.domain.return_costing import allocate_cents, returned_cents
from src.domain.uom import cost_groups
from src.infrastructure.return_models import ReturnModel as RMA, ReturnActionModel as Action, QuarantineModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.procurement_models import PurchaseOrderModel
from src.infrastructure.messaging.models import OutboxEventModel


class ReturnNotFound(ValueError):
    pass


def rma_dto(row):
    items = copy.deepcopy(row.items)
    if row.kind == 'SUPPLIER':
        for item in items:
            item['shippedQuantity'] = item.pop('receivedQuantity')
    return {'id': row.id, 'sourceMovementId': row.source_movement_id, 'kind': row.kind,
            'supplierId': row.supplier_id, 'status': row.status, 'version': row.version,
            'reason': row.reason, 'refundReference': row.refund_reference,
            'items': items, 'authorizedBy': row.authorized_by,
            'createdAt': utc(row.created_at).isoformat()}


def quarantine_dto(row):
    return {'id': row.id, 'returnId': row.return_id, 'sku': row.sku, 'locationId': row.location_id,
            'quantity': row.quantity, 'reason': row.reason, 'status': row.status,
            'version': row.version, 'createdAt': utc(row.created_at).isoformat()}


class ReturnService:
    def __init__(self, session, tenant, actor):
        self.session, self.tenant, self.actor = session, tenant, actor

    async def owned(self, model, identifier):
        row = await self.session.scalar(select(model).where(model.id == identifier, model.tenant_id == self.tenant))
        if not row:
            raise ReturnNotFound('Return, source or stock not found')
        return row

    async def execute(self, operation, data, key):
        fingerprint = {'operation': operation, **data}
        try:
            self.config = await locked_config(self.session, self.tenant)
            self.emit_event = True
            for field, model in [('return_id', RMA), ('sourceMovementId', StockMovementModel), ('quarantine_id', QuarantineModel)]:
                if data.get(field):
                    await self.owned(model, data[field])
            prior = await self.session.scalar(select(Action).where(Action.tenant_id == self.tenant, Action.mutation_id == key))
            if prior:
                if prior.request != fingerprint or prior.actor_id != self.actor:
                    raise StockConflict('Mutation ID was already used for another request')
                result = prior.result
                await self.session.rollback()
                return result
            result = await getattr(self, operation)(data, key)
            self.session.add(Action(tenant_id=self.tenant, mutation_id=key, actor_id=self.actor,
                                    request=fingerprint, result=result))
            if self.emit_event:
                self.session.add(OutboxEventModel(aggregate_type='Returns', aggregate_id=result.get('id', self.tenant),
                    event_type='ReturnWorkflowChanged', payload={'tenantId': self.tenant, 'actorId': self.actor,
                        'mutationId': key, 'operation': operation, 'result': result}))
            await self.session.commit()
            return result
        except IntegrityError as exc:
            await self.session.rollback()
            raise StockConflict('Concurrent return mutation; retry with the same mutation ID') from exc
        except Exception:
            await self.session.rollback()
            raise

    async def trace(self, movement, kind):
        if kind == 'SUPPLIER':
            reference = movement.result.get('reference', {})
            if movement.operation != 'receive' or not reference.get('purchaseOrderId'):
                raise StockConflict('Supplier returns require a PO-backed receipt')
            po = await self.owned(PurchaseOrderModel, reference['purchaseOrderId'])
            cost = movement.request.get('unitCostCents')
            return po.supplier_id, [{'layerId': movement.result['costLayerId'], 'quantity': movement.amount,
                                    'costCents': cost * movement.amount if cost is not None else None}]
        if movement.operation not in ('dispatch', 'fulfill-allocation', 'kit-sale-component'):
            raise StockConflict('Customer returns require a sale/fulfillment movement')
        entries = copy.deepcopy(movement.result.get('consumedLayers', []))
        if not entries or sum(i['quantity'] for i in entries) != abs(movement.amount):
            raise StockConflict('Historical sale has no complete returnable layer trace')
        if any('costCents' not in i for i in entries):
            total = movement.result.get('costOfGoodsSoldCents')
            if movement.result.get('costingMethod') == 'WAC':
                costs = allocate_cents(total, [i['quantity'] for i in entries])
            elif total is None:
                costs = [None] * len(entries)  # Never infer unrecorded historic COGS after later pricing.
            else:
                costs = []
                for entry in entries:
                    layer = await self.owned(CostLayerModel, entry['layerId'])
                    if layer.unit_cost_cents is None:
                        raise StockConflict('Historical sale cost breakdown is unavailable')
                    costs.append(layer.unit_cost_cents * entry['quantity'])
                if sum(costs) != total:
                    raise StockConflict('Historical sale cost breakdown does not reconcile')
            for entry, cost in zip(entries, costs):
                entry['costCents'] = cost
        return None, entries

    async def usage(self, source_id, exclude=None, reserve=False):
        totals = {}
        rows = await self.session.scalars(select(RMA).where(RMA.tenant_id == self.tenant,
                                                           RMA.source_movement_id == source_id))
        for row in rows:
            if row.id == exclude:
                continue
            for item in row.items:
                quantity = item['quantity'] if reserve and row.status in ('AUTHORIZED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'PARTIALLY_SHIPPED', 'SHIPPED') else item['receivedQuantity']
                totals[item['layerId']] = totals.get(item['layerId'], 0) + quantity
        return totals

    async def create(self, data, key):
        source = await self.owned(StockMovementModel, data['sourceMovementId'])
        supplier_id, entries = await self.trace(source, data['kind'])
        trace = {i['layerId']: i for i in entries}
        ids = [i['layerId'] for i in data['items']]
        if len(ids) != len(set(ids)):
            raise ValueError('Each source layer must occur once')
        items = []
        for item in data['items']:
            item = dict(item)
            item['disposition'] = item['disposition'] or ('RETURN_TO_VENDOR' if data['kind'] == 'SUPPLIER' else 'QUARANTINE')
            if (data['kind'] == 'SUPPLIER') != (item['disposition'] == 'RETURN_TO_VENDOR'):
                raise ValueError('Disposition does not match customer/supplier return kind')
            original = trace.get(item['layerId'])
            if not original or item['quantity'] > original['quantity']:
                raise StockConflict('Return exceeds source layer quantity')
            layer = await self.owned(CostLayerModel, item['layerId'])
            if layer.sku != source.sku or layer.location_id != source.location_id:
                raise StockConflict('Source layer does not match movement')
            items.append({**item, 'originalQuantity': original['quantity'], 'originalCostCents': original['costCents'],
                          'receivedQuantity': 0})
        row = RMA(id=str(uuid.uuid4()), tenant_id=self.tenant, source_movement_id=source.id,
            kind=data['kind'], supplier_id=supplier_id, status='REQUESTED', version=0,
            reason=data['reason'], refund_reference=data.get('refundReference'), items=items,
            created_by=self.actor, created_at=datetime.now(timezone.utc))
        self.session.add(row)
        return rma_dto(row)

    async def transition(self, data, key):
        row = await self.owned(RMA, data['return_id'])
        if row.version != data['expectedVersion']:
            raise StockConflict('Return version conflict')
        target = data['target']
        if row.status == target:
            self.emit_event = False
            return rma_dto(row)
        if target == 'CANCELLED':
            if row.status in ('RECEIVED', 'SHIPPED'):
                raise StockConflict('Completed return cannot be cancelled')
        else:
            if row.status != 'REQUESTED':
                raise StockConflict('Only requested returns can be authorized')
            used = await self.usage(row.source_movement_id, row.id, reserve=True)
            for item in row.items:
                if used.get(item['layerId'], 0) + item['quantity'] > item['originalQuantity']:
                    raise StockConflict('Other authorized/received returns exhausted source quantity')
            row.authorized_by = self.actor
        row.status, row.version = target, row.version + 1
        return rma_dto(row)

    async def change_stock(self, stock, delta, operation, details, key):
        quantity, version = stock.quantity + delta, stock.version
        if quantity < stock.allocated:
            raise StockConflict('Return/write-off would remove reserved stock')
        if not 0 <= quantity <= 2_147_483_647:
            raise ValueError('Adjusted quantity is outside stock limits')
        changed = await self.session.execute(update(StockModel).where(StockModel.id == stock.id,
            StockModel.tenant_id == self.tenant, StockModel.version == version).values(quantity=quantity, version=version + 1))
        if changed.rowcount != 1:
            raise StockConflict('Stock version conflict')
        result = {**stock_dto(stock), 'quantity': quantity, 'available': quantity - stock.allocated,
                  'version': version + 1, 'delta': delta, **details}
        self.session.add(StockMovementModel(tenant_id=self.tenant, mutation_id='returns:' + key,
            actor_id=self.actor, sku=stock.sku, location_id=stock.location_id, operation=operation,
            amount=delta, request=details, result=result))
        self.session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=stock.id,
            event_type='StockReturnAdjusted', payload={**result, 'tenantId': self.tenant, 'actorId': self.actor,
                                                      'mutationId': key, 'operation': operation}))
        await self.session.flush()
        return result

    async def remove(self, stock, quantity, layer_ids=None, lot=None):
        if stock.quantity - stock.allocated < quantity:
            raise StockConflict('Insufficient unreserved stock')
        layers = await load_layers(self.session, self.tenant, stock)
        cost, used = consume(layers, quantity, self.config.costing_method, lot_number=lot,
                            include_ineligible=True, layer_ids=layer_ids)
        if sum(l.remaining_quantity for l in eligible_layers(layers, self.config.costing_method)) < stock.allocated:
            raise StockConflict('Loss would consume stock backing existing reservations')
        quarantine_changes = await record_quarantine_losses(self.session, self.tenant, used)
        return cost, used, quarantine_changes

    async def restore(self, stock, source_layer, quantity, value, quarantined):
        layers = await load_layers(self.session, self.tenant, stock)
        groups = cost_groups(quantity, value)
        created = []
        for count, cost in groups:
            layer = CostLayerModel(id=str(uuid.uuid4()), stock_id=stock.id, tenant_id=self.tenant,
                sku=stock.sku, location_id=stock.location_id, initial_quantity=count, remaining_quantity=count,
                unit_cost_cents=cost, initial_value_cents=value, remaining_value_cents=value,
                lot_number=source_layer.lot_number, expiration_date=source_layer.expiration_date,
                received_at=datetime.now(timezone.utc), quarantined=quarantined)
            self.session.add(layer)
            layers.append(layer)
            created.append(layer.id)
        # Bolt Optimization: Single-pass list aggregation for WAC costing
        # Impact: Replaces two separate O(N) traversals (sum, all) with a single pass
        total = 0
        wac_priced = True
        for layer in layers:
            total += layer.remaining_value_cents or 0
            if layer.remaining_quantity and layer.remaining_value_cents is None:
                wac_priced = False
        validate_total_value(total)
        if self.config.costing_method == 'WAC' and wac_priced:
            redistribute_value(layers, total)
        return created

    async def receive(self, data, key):
        row = await self.owned(RMA, data['return_id'])
        if row.status not in ('AUTHORIZED', 'PARTIALLY_RECEIVED', 'PARTIALLY_SHIPPED'):
            raise StockConflict('Return is not open for receipt/shipment')
        if row.version != data['expectedVersion']:
            raise StockConflict('Return version conflict')
        source = await self.owned(StockMovementModel, row.source_movement_id)
        stock = await self.owned(StockModel, source.result['id'])
        items = copy.deepcopy(row.items)
        by_id = {i['layerId']: i for i in items}
        ids = [i['layerId'] for i in data['items']]
        if len(ids) != len(set(ids)):
            raise ValueError('Each returned source layer must occur once')
        received = await self.usage(source.id)
        movements = []
        for entry in data['items']:
            item, quantity = by_id.get(entry['layerId']), entry['quantity']
            if not item or quantity > item['quantity'] - item['receivedQuantity']:
                raise StockConflict('Receipt exceeds authorized return balance')
            layer = await self.owned(CostLayerModel, entry['layerId'])
            value = returned_cents(item['originalCostCents'], item['originalQuantity'], received.get(layer.id, 0), quantity)
            details = {'returnId': row.id, 'sourceMovementId': source.id, 'sourceLayerId': layer.id,
                       'returnedQuantity': quantity, 'reason': row.reason, 'refundReference': row.refund_reference}
            delta = quantity
            if row.kind == 'SUPPLIER':
                cost, used, changes = await self.remove(stock, quantity, {layer.id})
                delta = -quantity
                details.update(inventoryValueChangeCents=-cost if cost is not None else None,
                    supplierCreditExpectedCents=value, supplierId=row.supplier_id, consumedLayers=used,
                    creditVarianceCents=value - cost if value is not None and cost is not None else None,
                    quarantineChanges=changes, cogsReversedCents=0)
            else:
                disposition = item['disposition']
                created = []
                if disposition == 'RESTOCK' and (layer.quarantined or (layer.expiration_date and utc(layer.expiration_date) <= datetime.now(timezone.utc))):
                    raise StockConflict('Expired/quarantined goods require quarantine or scrap')
                if disposition == 'SCRAP':
                    delta = 0
                else:
                    created = await self.restore(stock, layer, quantity, value, disposition == 'QUARANTINE')
                details.update(cogsReversedCents=value, inventoryValueChangeCents=value if delta else 0,
                    writeOffCents=value if disposition == 'SCRAP' else 0, disposition=disposition, restoredLayerIds=created)
                if disposition == 'QUARANTINE':
                    quarantine = QuarantineModel(id=str(uuid.uuid4()), tenant_id=self.tenant, return_id=row.id,
                        stock_id=stock.id, sku=stock.sku, location_id=stock.location_id, quantity=quantity,
                        reason=row.reason, layer_ids=created, status='QUARANTINED', version=0,
                        created_at=datetime.now(timezone.utc))
                    self.session.add(quarantine)
                    details['quarantineId'] = quarantine.id
            movements.append(await self.change_stock(stock, delta,
                'customer-return' if row.kind == 'CUSTOMER' else 'supplier-return', details, key + ':' + layer.id))
            item['receivedQuantity'] += quantity
            received[layer.id] = received.get(layer.id, 0) + quantity
        row.items, row.version = items, row.version + 1
        completed = all(i['receivedQuantity'] == i['quantity'] for i in items)
        row.status = ('SHIPPED' if completed else 'PARTIALLY_SHIPPED') if row.kind == 'SUPPLIER' else (
            'RECEIVED' if completed else 'PARTIALLY_RECEIVED')
        return {**rma_dto(row), 'stockResults': movements,
                'shipmentId' if row.kind == 'SUPPLIER' else 'receiptId': data['receiptId']}

    async def ship(self, data, key):
        row = await self.owned(RMA, data['return_id'])
        if row.kind != 'SUPPLIER':
            raise StockConflict('Only supplier returns can be shipped')
        return await self.receive(data, key)

    async def write_off(self, data, key):
        data = dict(data)
        conversion = None
        if data.get('unit'):
            from src.application.uom_service import convert_input
            data['quantity'], conversion = await convert_input(self.session, self.tenant, data['sku'],
                data['quantity'], data['unit'], data.get('uomVersion'))
        stock = await self.session.scalar(select(StockModel).where(StockModel.tenant_id == self.tenant,
            StockModel.sku == data['sku'], StockModel.location_id == data['locationId']))
        if not stock:
            raise ReturnNotFound('Stock not found')
        if stock.version != data['expectedVersion']:
            raise StockConflict('Stock version conflict')
        cost, used, changes = await self.remove(stock, data['quantity'],
            {data['layerId']} if data.get('layerId') else None, data.get('lotNumber'))
        return await self.change_stock(stock, -data['quantity'], 'write-off', {
            'reason': data['reason'], 'reasonCode': data['reasonCode'], 'reference': data.get('reference'),
            'inventoryValueChangeCents': -cost if cost is not None else None,
            'writeOffCents': cost, 'cogsReversedCents': 0, 'consumedLayers': used,
            'quarantineChanges': changes, 'inputUom': conversion}, key)

    async def resolve(self, data, key):
        row = await self.owned(QuarantineModel, data['quarantine_id'])
        if row.version != data['expectedVersion'] or row.status != 'QUARANTINED':
            raise StockConflict('Quarantine item is resolved or its version is stale')
        stock = await self.owned(StockModel, row.stock_id)
        layers = list(await self.session.scalars(select(CostLayerModel).where(
            CostLayerModel.tenant_id == self.tenant, CostLayerModel.id.in_(row.layer_ids))))
        if sum(l.remaining_quantity for l in layers) != row.quantity:
            raise StockConflict('Quarantine quantity changed; reconcile before resolving')
        if data['resolution'] == 'RESTOCK':
            if any(l.expiration_date and utc(l.expiration_date) <= datetime.now(timezone.utc) for l in layers if l.remaining_quantity):
                raise StockConflict('Expired quarantine cannot be restocked')
            for layer in layers:
                layer.quarantined = False
            row.status, row.version = 'RESTOCKED', row.version + 1
            await self.change_stock(stock, 0, 'quarantine-restock', {'quarantineId': row.id,
                'reason': data['reason'], 'inventoryValueChangeCents': 0, 'cogsReversedCents': 0}, key)
        else:
            quantity = row.quantity
            cost, used, changes = await self.remove(stock, quantity, set(row.layer_ids))
            await self.change_stock(stock, -quantity, 'write-off', {'quarantineId': row.id,
                'reason': data['reason'], 'reasonCode': 'QUARANTINE_SCRAP', 'inventoryValueChangeCents': -cost if cost is not None else None,
                'writeOffCents': cost, 'cogsReversedCents': 0, 'consumedLayers': used, 'quarantineChanges': changes}, key)
        return quarantine_dto(row)
