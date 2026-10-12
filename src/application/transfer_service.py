import copy
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.application.costing_service import locked_config, load_layers
from src.application.stock_service import StockConflict, stock_dto
from src.application.uom_service import owned_product, convert_input, save_uom, configured
from src.domain.product.layer_costing import consume, eligible_layers, redistribute_value, utc, validate_total_value
from src.domain.uom import cost_groups
from src.infrastructure.transfer_models import TransferModel, InventoryActionModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.messaging.models import OutboxEventModel


def transfer_dto(row):
    return {'id': row.id, 'sourceLocationId': row.source_location_id,
            'destinationLocationId': row.destination_location_id, 'status': row.status,
            'version': row.version, 'items': row.items, 'reference': row.reference,
            'createdAt': utc(row.created_at).isoformat(),
            'dispatchedAt': utc(row.dispatched_at).isoformat() if row.dispatched_at else None,
            'receivedAt': utc(row.received_at).isoformat() if row.received_at else None}


def transfer_response(request, result):
    if 'accounting:read' in request.state.permissions:
        return result
    # Transit snapshots and stock results can contain costs at several depths.
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items() if not key.endswith('Cents')}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    return redact(result)


class InventoryWorkflow:
    def __init__(self, session, tenant, actor):
        self.session, self.tenant, self.actor = session, tenant, actor

    async def owned_transfer(self, identifier):
        row = await self.session.scalar(select(TransferModel).where(TransferModel.id == identifier, TransferModel.tenant_id == self.tenant))
        if not row:
            raise LookupError('Transfer not found')
        return row

    async def execute(self, operation, data, key):
        try:
            self.config = await locked_config(self.session, self.tenant)
            if data.get('transferId'):
                await self.owned_transfer(data['transferId'])
            if operation == 'configure_uom':
                await owned_product(self.session, self.tenant, data['sku'])
            fingerprint = {'operation': operation, **data}
            prior = await self.session.scalar(select(InventoryActionModel).where(
                InventoryActionModel.tenant_id == self.tenant, InventoryActionModel.mutation_id == key))
            if prior:
                if prior.request != fingerprint or prior.actor_id != self.actor:
                    raise StockConflict('Mutation ID was already used for another request')
                result = prior.result
                await self.session.rollback()
                return result
            result = await getattr(self, operation)(data, key)
            self.session.add(InventoryActionModel(tenant_id=self.tenant, mutation_id=key, actor_id=self.actor,
                request=fingerprint, result=result))
            self.session.add(OutboxEventModel(aggregate_type='Inventory', aggregate_id=result['id'],
                event_type='InventoryWorkflowChanged', payload={'tenantId': self.tenant, 'actorId': self.actor,
                    'mutationId': key, 'operation': operation, 'result': result}))
            await self.session.commit()
            return result
        except IntegrityError as exc:
            await self.session.rollback()
            raise StockConflict('Concurrent inventory mutation; retry with the same mutation ID') from exc
        except Exception:
            await self.session.rollback()
            raise

    async def configure_uom(self, data, key):
        return await save_uom(self.session, self.tenant, data)

    async def create_transfer(self, data, key):
        if data['sourceLocationId'] == data['destinationLocationId']:
            raise ValueError('Source and destination locations must differ')
        skus = [item['sku'] for item in data['items']]
        if len(skus) != len(set(skus)):
            raise ValueError('Each SKU must occur once per transfer')
        items = []
        for item in data['items']:
            await owned_product(self.session, self.tenant, item['sku'])
            unit, quantity = item.get('unit'), item['quantity']
            conversion = {}
            if unit:
                quantity, conversion = await convert_input(self.session, self.tenant, item['sku'], quantity, unit, item.get('uomVersion'))
            elif type(quantity) is not int or not 0 < quantity <= 2_147_483_647:
                raise ValueError('Base quantity must be a positive integer')
            config = await configured(self.session, self.tenant, item['sku'])
            items.append({'sku': item['sku'], 'quantity': quantity, 'lotNumber': item.get('lotNumber'),
                          'baseUnit': config.base_unit if config else 'ea', 'input': conversion, 'batches': []})
        row = TransferModel(id=str(uuid.uuid4()), tenant_id=self.tenant,
            source_location_id=data['sourceLocationId'], destination_location_id=data['destinationLocationId'],
            items=items, status='DRAFT', version=0, reference=data.get('reference'), created_by=self.actor,
            created_at=datetime.now(timezone.utc))
        self.session.add(row)
        return transfer_dto(row)

    async def stock(self, sku, location, create=False):
        stock = await self.session.scalar(select(StockModel).where(StockModel.tenant_id == self.tenant,
            StockModel.sku == sku, StockModel.location_id == location))
        if not stock:
            if not create:
                raise LookupError('Stock not found at source location')
            stock = StockModel(id=str(uuid.uuid4()), tenant_id=self.tenant, sku=sku,
                location_id=location, quantity=0, allocated=0, version=0)
            self.session.add(stock)
            await self.session.flush()
        return stock

    async def record(self, stock, delta, operation, key, details):
        version, quantity = stock.version, stock.quantity + delta
        if not stock.allocated <= quantity <= 2_147_483_647:
            raise StockConflict('Transfer exceeds stock limits or removes reserved stock')
        changed = await self.session.execute(update(StockModel).where(StockModel.id == stock.id,
            StockModel.tenant_id == self.tenant, StockModel.version == version).values(quantity=quantity, version=version + 1))
        if changed.rowcount != 1:
            raise StockConflict('Stock version conflict')
        result = {**stock_dto(stock), 'quantity': quantity, 'version': version + 1,
                  'available': quantity - stock.allocated, 'delta': delta, 'costOfGoodsSoldCents': 0, **details}
        self.session.add(StockMovementModel(tenant_id=self.tenant, mutation_id='transfer:' + key + ':' + stock.id,
            actor_id=self.actor, sku=stock.sku, location_id=stock.location_id, operation=operation,
            amount=delta, request=details, result=result))
        self.session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=stock.id,
            event_type='StockTransferred', payload={**result, 'tenantId': self.tenant, 'actorId': self.actor}))
        await self.session.flush()
        return result

    async def receive_batches(self, stock, batches):
        layers = await load_layers(self.session, self.tenant, stock)
        for batch in batches:
            for quantity, cost in cost_groups(batch['quantity'], batch['costCents']):
                layer = CostLayerModel(id=str(uuid.uuid4()), tenant_id=self.tenant, stock_id=stock.id,
                    sku=stock.sku, location_id=stock.location_id, initial_quantity=quantity,
                    remaining_quantity=quantity, unit_cost_cents=cost,
                    initial_value_cents=batch['costCents'], remaining_value_cents=batch['costCents'],
                    lot_number=batch['lotNumber'], expiration_date=datetime.fromisoformat(batch['expirationDate']) if batch['expirationDate'] else None,
                    received_at=datetime.fromisoformat(batch['receivedAt']), quarantined=False)
                self.session.add(layer)
                layers.append(layer)
        total = sum(layer.remaining_value_cents or 0 for layer in layers)
        validate_total_value(total)
        if self.config.costing_method == 'WAC' and all(l.remaining_value_cents is not None for l in layers if l.remaining_quantity):
            redistribute_value(layers, total)

    async def transition_transfer(self, data, key):
        row = await self.owned_transfer(data['transferId'])
        if row.version != data['expectedVersion']:
            raise StockConflict('Transfer version conflict')
        target = data['target']
        if target == 'DISPATCHED' and row.status != 'DRAFT' or target == 'RECEIVED' and row.status != 'DISPATCHED' or target == 'CANCELLED' and row.status not in ('DRAFT', 'DISPATCHED'):
            raise StockConflict('Transfer cannot perform this transition')
        items, results = copy.deepcopy(row.items), []
        if target == 'DISPATCHED':
            versions = data.get('sourceVersions') or {}
            if set(versions) != {item['sku'] for item in items}:
                raise ValueError('Dispatch requires a source stock version for every SKU')
            for item in items:
                stock = await self.stock(item['sku'], row.source_location_id)
                if stock.version != versions[item['sku']]:
                    raise StockConflict('Source stock version conflict')
                if stock.quantity - stock.allocated < item['quantity']:
                    raise StockConflict('Insufficient available source stock')
                layers = await load_layers(self.session, self.tenant, stock)
                lookup = {layer.id: layer for layer in layers}
                value, used = consume(layers, item['quantity'], self.config.costing_method, lot_number=item['lotNumber'])
                if sum(l.remaining_quantity for l in eligible_layers(layers, self.config.costing_method)) < stock.allocated:
                    raise StockConflict('Transfer would consume eligible stock backing reservations')
                for entry in used:
                    layer = lookup[entry['layerId']]
                    item['batches'].append({**entry, 'expirationDate': utc(layer.expiration_date).isoformat() if layer.expiration_date else None,
                        'receivedAt': utc(layer.received_at).isoformat()})
                results.append(await self.record(stock, -item['quantity'], 'transfer-dispatch', key,
                    {'transferId': row.id, 'inventoryValueChangeCents': -value if value is not None else None,
                     'transferredValueCents': value, 'reason': data['reason']}))
            row.dispatched_at = datetime.now(timezone.utc)
        elif row.status == 'DISPATCHED':
            location = row.destination_location_id if target == 'RECEIVED' else row.source_location_id
            for item in items:
                stock = await self.stock(item['sku'], location, create=True)
                await self.receive_batches(stock, item['batches'])
                value = None if any(b['costCents'] is None for b in item['batches']) else sum(b['costCents'] for b in item['batches'])
                results.append(await self.record(stock, item['quantity'], 'transfer-receive' if target == 'RECEIVED' else 'transfer-cancel', key,
                    {'transferId': row.id, 'inventoryValueChangeCents': value, 'reason': data['reason']}))
            if target == 'RECEIVED':
                row.received_at = datetime.now(timezone.utc)
        row.status, row.version, row.items = target, row.version + 1, items
        return {**transfer_dto(row), 'stockResults': results}
