import copy
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update, func
from src.application.transfer_service import InventoryWorkflow
from src.application.tracking_service import enable_serial_tracking, require_quantity_tracking
from src.application.uom_service import owned_product
from src.application.costing_service import load_layers
from src.application.stock_service import StockConflict, stock_dto
from src.domain.product.layer_costing import consume, eligible_layers, eligible_quantity, utc, validate_total_value
from src.infrastructure.serial_kit_models import SerialModel, KitModel, KitBuildModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.messaging.models import OutboxEventModel


TRANSITIONS = {'pending': {'in_stock', 'quarantined', 'written_off'},
    'in_stock': {'sold', 'transferred', 'quarantined', 'damaged', 'written_off'},
    'sold': {'returned'}, 'returned': {'in_stock', 'quarantined', 'damaged', 'written_off'},
    'quarantined': {'in_stock', 'damaged', 'written_off'},
    'transferred': {'in_stock', 'quarantined', 'damaged'},
    'damaged': {'quarantined', 'written_off'}, 'written_off': set()}


def serial_dto(row):
    return {'id': row.id, 'tenantId': row.tenant_id, 'serialNumber': row.serial_number, 'variantId': row.variant_id,
        'sku': row.sku, 'locationId': row.location_id, 'destinationId': row.destination_id,
        'status': row.status, 'version': row.version, 'reservation': row.reservation,
        'costCents': row.cost_cents, 'history': [{**h, 'from': h['fromStatus'], 'to': h['toStatus'], 'actor': h['actorId']} for h in row.history]}


def kit_dto(row):
    return {'id': row.id, 'sku': row.sku, 'name': row.name, 'components': row.components, 'version': row.version}


class SerialKitWorkflow(InventoryWorkflow):
    async def execute(self, operation, data, key):
        if operation in ('define_serial', 'define_kit'):
            await owned_product(self.session, self.tenant, data['sku'])
        if operation == 'change_serial':
            await self.serial(data['serialNumber'])
        if operation == 'use_kit':
            if not await self.session.scalar(select(KitModel.id).where(KitModel.tenant_id == self.tenant, KitModel.sku == data['kitSku'])):
                raise LookupError('Kit not found')
        return await super().execute(operation, data, 'serial-kit:' + key)

    async def serial(self, number):
        row = await self.session.scalar(select(SerialModel).where(SerialModel.tenant_id == self.tenant, SerialModel.serial_number == number))
        if row is None:
            raise LookupError('Serial not found')
        return row

    async def define_serial(self, data, key):
        await enable_serial_tracking(self.session, self.tenant, data['sku'])
        product = await owned_product(self.session, self.tenant, data['sku'])
        if await self.session.scalar(select(SerialModel.id).where(SerialModel.tenant_id == self.tenant, SerialModel.serial_number == data['serialNumber'])):
            raise StockConflict('Serial number is already registered')
        row = SerialModel(id=str(uuid.uuid4()), tenant_id=self.tenant, sku=data['sku'],
            serial_number=data['serialNumber'], variant_id=product.id, location_id=data['locationId'], status='pending', version=0,
            history=[{'toStatus': 'pending', 'fromStatus': None, 'actorId': self.actor,
                'reason': data['reason'], 'referenceId': data.get('referenceId'), 'occurredAt': datetime.now(timezone.utc).isoformat()}])
        self.session.add(row)
        return serial_dto(row)

    async def serial_record(self, stock, delta, allocation, key, details):
        validate_total_value(await self.session.scalar(select(func.coalesce(func.sum(CostLayerModel.remaining_value_cents), 0))
            .where(CostLayerModel.tenant_id == self.tenant, CostLayerModel.stock_id == stock.id)))
        quantity, allocated = stock.quantity + delta, stock.allocated + allocation
        if not 0 <= allocated <= quantity <= 2_147_483_647:
            raise StockConflict('Serial command exceeds available or allocated stock')
        version = stock.version
        changed = await self.session.execute(update(StockModel).where(StockModel.id == stock.id,
            StockModel.tenant_id == self.tenant, StockModel.version == version)
            .values(quantity=quantity, allocated=allocated, version=version + 1))
        if changed.rowcount != 1:
            raise StockConflict('Stock version conflict')
        result = {**stock_dto(stock), 'quantity': quantity, 'allocated': allocated,
            'available': quantity - allocated, 'version': version + 1, **details}
        self.session.add(StockMovementModel(tenant_id=self.tenant, actor_id=self.actor,
            mutation_id='serial:' + key + ':' + stock.id, sku=stock.sku, location_id=stock.location_id,
            operation=details['operation'], amount=delta, request=details, result=result))
        self.session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=stock.id,
            event_type='SerializedStockChanged', payload={**result, 'tenantId': self.tenant, 'actorId': self.actor}))
        return result

    async def serial_layer(self, row, stock, data, quarantined):
        value = row.cost_cents
        layer = CostLayerModel(id=str(uuid.uuid4()), stock_id=stock.id, tenant_id=self.tenant,
            sku=row.sku, location_id=stock.location_id, initial_quantity=1, remaining_quantity=1,
            unit_cost_cents=value, initial_value_cents=value, remaining_value_cents=value,
            received_at=datetime.now(timezone.utc), quarantined=quarantined,
            expiration_date=datetime.fromisoformat(data['expirationDate']) if isinstance(data.get('expirationDate'), str) else data.get('expirationDate'))
        self.session.add(layer)
        row.layer_id = layer.id
        return layer

    async def change_serial(self, data, key):
        row = await self.serial(data['serialNumber'])
        if row.version != data['expectedVersion']:
            raise StockConflict('Serial version conflict')
        action = data['action']
        old = row.status
        if data.get('expirationDate') is not None and not (old == 'pending' and action in ('receive', 'quarantine')):
            raise ValueError('Expiration metadata is only supported for initial receiving')
        if data.get('destinationId') is not None and action != 'transfer':
            raise ValueError('Destination is only supported for serial transfers')
        if data.get('locationId') is not None and action not in ('receive', 'return'):
            raise ValueError('Location is only supported for receiving or returns')
        if data.get('unitCostCents') is not None and action != 'price' and not (old == 'pending' and action in ('receive', 'quarantine')):
            raise ValueError('Cost metadata is only supported for initial receiving or explicit pricing')
        details = {'operation': 'serial-' + action, 'serialNumber': row.serial_number, 'fromStatus': old, 'costOfGoodsSoldCents': 0}
        if action == 'price':
            if old not in ('in_stock', 'returned', 'quarantined', 'damaged') or row.cost_cents is not None or data.get('unitCostCents') is None:
                raise StockConflict('Only an unpriced serial physically on hand can be priced')
            stock = await self.stock(row.sku, row.location_id)
            layer = await self.session.scalar(select(CostLayerModel).where(CostLayerModel.id == row.layer_id,
                CostLayerModel.tenant_id == self.tenant, CostLayerModel.stock_id == stock.id))
            if layer is None or layer.remaining_quantity != 1 or layer.unit_cost_cents is not None:
                raise StockConflict('Serial cost layer does not match unpriced physical inventory')
            value = data['unitCostCents']
            row.cost_cents = layer.unit_cost_cents = layer.initial_value_cents = layer.remaining_value_cents = value
            details['inventoryValueChangeCents'] = value
            await self.serial_record(stock, 0, 0, key, details)
            target = old
        elif action in ('reserve', 'release'):
            if old != 'in_stock' or action == 'reserve' and row.reservation or action == 'release' and row.reservation != data['referenceId']:
                raise StockConflict('Serial reservation does not match current state')
            stock = await self.stock(row.sku, row.location_id)
            layer = await self.session.scalar(select(CostLayerModel).where(CostLayerModel.id == row.layer_id,
                CostLayerModel.tenant_id == self.tenant, CostLayerModel.stock_id == stock.id))
            if not layer or layer.remaining_quantity != 1:
                raise StockConflict('Serial cost layer does not match physical inventory')
            if action == 'reserve' and not eligible_layers([layer], 'FIFO'):
                raise StockConflict('Serial is not eligible for reservation')
            await self.serial_record(stock, 0, 1 if action == 'reserve' else -1, key, details)
            row.reservation = data['referenceId'] if action == 'reserve' else None
            target = old
        else:
            targets = {'receive': 'in_stock', 'sell': 'sold', 'return': 'returned', 'restock': 'in_stock',
                'quarantine': 'quarantined', 'damage': 'damaged', 'write-off': 'written_off', 'transfer': 'transferred'}
            target = targets[action]
            if target not in TRANSITIONS[old] or action == 'receive' and old not in ('pending', 'transferred') or action == 'restock' and old in ('pending', 'transferred'):
                raise StockConflict('Invalid serial status transition')
            if row.reservation and target != 'sold':
                raise StockConflict('Release the serial reservation before changing its physical state')
            if data.get('unitCostCents') is not None and old != 'pending':
                raise ValueError('Only initial receiving may supply purchase cost')
            if old == 'pending' and target != 'written_off' or old in ('sold', 'transferred'):
                location = row.destination_id if old == 'transferred' else row.location_id
                if data.get('locationId') and data['locationId'] != location:
                    raise ValueError('Receiving location must match the serial destination')
                stock = await self.stock(row.sku, location, create=True)
                if old == 'pending':
                    row.cost_cents = data.get('unitCostCents')
                if old in ('sold', 'transferred'):
                    original = await self.session.scalar(select(CostLayerModel).where(CostLayerModel.id == row.layer_id, CostLayerModel.tenant_id == self.tenant))
                    if original is None:
                        raise StockConflict('Serial source cost layer is missing')
                    data = {**data, 'expirationDate': original.expiration_date}
                layer = await self.serial_layer(row, stock, data, target != 'in_stock')
                row.location_id, row.destination_id = location, None
                if old == 'sold':
                    details['cogsReversedCents'] = row.cost_cents
                    details['sourceLayerId'] = original.id
                details.update(receiptValueCents=row.cost_cents, costLayerId=layer.id)
                await self.serial_record(stock, 1, 0, key, details)
            elif old != 'pending':
                stock = await self.stock(row.sku, row.location_id)
                layer = await self.session.scalar(select(CostLayerModel).where(CostLayerModel.id == row.layer_id,
                    CostLayerModel.tenant_id == self.tenant, CostLayerModel.stock_id == stock.id))
                if not layer or layer.remaining_quantity != 1:
                    raise StockConflict('Serial cost layer does not match physical inventory')
                if target == 'sold' and (not eligible_layers([layer], 'FIFO') or row.reservation and row.reservation != data['referenceId']):
                    raise StockConflict('Serial is unavailable or reserved for another sale')
                if target == 'transferred' and (row.reservation or not data.get('destinationId') or data['destinationId'] == row.location_id):
                    raise ValueError('Transfer requires another destination and an unreserved serial')
                allocation = -1 if row.reservation else 0
                if target in ('sold', 'written_off', 'transferred'):
                    details['sourceCostLayerId'] = layer.id
                    row.cost_cents = layer.remaining_value_cents
                    layer.remaining_quantity, layer.remaining_value_cents = 0, 0
                    delta = -1
                    details['costOfGoodsSoldCents'] = row.cost_cents if target == 'sold' else 0
                    if target == 'written_off':
                        details['inventoryValueChangeCents'] = -row.cost_cents if row.cost_cents is not None else None
                    if target == 'transferred':
                        row.destination_id = data['destinationId']
                        details['transferredValueCents'] = row.cost_cents
                else:
                    delta = 0
                    layer.quarantined = target != 'in_stock'
                await self.serial_record(stock, delta, allocation, key, details)
                row.reservation = None
        history = copy.deepcopy(row.history)
        history.append({'fromStatus': old, 'toStatus': target, 'action': action, 'reason': data['reason'],
            'referenceId': data['referenceId'], 'actorId': self.actor, 'occurredAt': datetime.now(timezone.utc).isoformat(),
            'locationId': row.location_id, 'destinationId': row.destination_id})
        row.status, row.version, row.history = target, row.version + 1, history
        return serial_dto(row)

    async def define_kit(self, data, key):
        await owned_product(self.session, self.tenant, data['sku'])
        await require_quantity_tracking(self.session, self.tenant, data['sku'])
        from src.application.uom_service import configured
        unit = await configured(self.session, self.tenant, data['sku'])
        if unit and (unit.category != 'discrete' or unit.base_unit != 'ea'):
            raise ValueError('Kit output must use discrete eaches')
        skus = [c['sku'] for c in data['components']]
        if len(skus) != len(set(skus)) or data['sku'] in skus:
            raise ValueError('Kit components must be distinct and exclude the kit itself')
        kits = list(await self.session.scalars(select(KitModel).where(KitModel.tenant_id == self.tenant)))
        if any(k.sku in skus or k.sku != data['sku'] and any(c['sku'] == data['sku'] for c in k.components) for k in kits):
            raise ValueError('Nested kits are not supported')
        for sku in skus:
            await owned_product(self.session, self.tenant, sku)
            await require_quantity_tracking(self.session, self.tenant, sku)
        row = next((k for k in kits if k.sku == data['sku']), None)
        if data['expectedVersion'] != (row.version if row else 0):
            raise StockConflict('Kit formula version conflict')
        if not row:
            row = KitModel(id=str(uuid.uuid4()), tenant_id=self.tenant, sku=data['sku'], version=0)
            self.session.add(row)
        row.name, row.components, row.version = data['name'], data['components'], row.version + 1
        return kit_dto(row)

    async def use_kit(self, data, key):
        row = await self.session.scalar(select(KitModel).where(KitModel.tenant_id == self.tenant, KitModel.sku == data['kitSku']))
        if row is None:
            raise LookupError('Kit not found')
        if row.version != data['expectedVersion']:
            raise StockConflict('Kit formula version conflict')
        location, action, quantity = data['locationId'], data['action'], data['quantity']
        output = await self.stock(row.sku, location, create=True)
        components = {c['sku']: await self.stock(c['sku'], location) for c in row.components} if action != 'disassemble' else {}
        expected = data['stockVersions']
        stocks = {**components, row.sku: output} if action != 'sale' else components
        if action == 'disassemble':
            stocks = {row.sku: output}
        if set(expected) != set(stocks) or any(s.version != expected[sku] for sku, s in stocks.items()):
            raise StockConflict('Supply current stock versions for every affected source SKU')
        total, restored = 0, []
        if action == 'disassemble':
            layers = await load_layers(self.session, self.tenant, output)
            builds = list(await self.session.scalars(select(KitBuildModel).where(KitBuildModel.tenant_id == self.tenant,
                KitBuildModel.kit_id == row.id, KitBuildModel.disassembled.is_(False)).order_by(KitBuildModel.id)))
            lookup = {l.id: l for l in layers if l.remaining_quantity == 1 and eligible_layers([l], 'FIFO')}
            selected = [b for b in builds if b.layer_id in lookup][:quantity]
            if len(selected) != quantity or output.quantity - output.allocated < quantity:
                raise StockConflict('Insufficient unreserved, traceable assembled kits')
            from src.domain.return_costing import allocate_cents
            for build in selected:
                layer = lookup[build.layer_id]
                value = layer.remaining_value_cents
                if value is None:
                    raise ValueError('Price assembled kits before disassembly')
                costs = allocate_cents(value, [c['costCents'] for c in build.components]) if any(c['costCents'] for c in build.components) else allocate_cents(value, [c['quantity'] for c in build.components])
                layer.remaining_quantity, layer.remaining_value_cents = 0, 0
                total += value
                for index, (component, cost) in enumerate(zip(build.components, costs)):
                    await owned_product(self.session, self.tenant, component['sku'])
                    stock = await self.stock(component['sku'], location, create=True)
                    batch = {**component, 'costCents': cost}
                    await self.receive_batches(stock, [batch])
                    await self.record(stock, component['quantity'], 'kit-disassemble-component', key + ':' + build.id + ':' + str(index), {'kitSku': row.sku, 'carryingValueCents': cost})
                    restored.append(batch)
                build.disassembled = True
            await self.record(output, -quantity, 'kit-disassemble', key, {'carryingValueCents': total})
        else:
            for _ in range(quantity if action == 'assemble' else 1):
                manifest, unit_total = [], 0
                for c in row.components:
                    stock = components[c['sku']]
                    needed = c['quantity'] * (quantity if action == 'sale' else 1)
                    if stock.quantity - stock.allocated < needed:
                        raise StockConflict('Insufficient unreserved kit components')
                    layers = await load_layers(self.session, self.tenant, stock)
                    lookup = {l.id: l for l in layers}
                    value, used = consume(layers, needed, self.config.costing_method)
                    if value is None:
                        raise ValueError('Price kit components before kit operations')
                    if eligible_quantity(layers, self.config.costing_method) < stock.allocated:
                        raise StockConflict('Kit consumption would remove stock backing reservations')
                    for entry in used:
                        original = lookup[entry['layerId']]
                        manifest.append({'sku': c['sku'], 'quantity': entry['quantity'], 'costCents': entry['costCents'],
                            'lotNumber': original.lot_number, 'expirationDate': utc(original.expiration_date).isoformat() if original.expiration_date else None,
                            'receivedAt': utc(original.received_at).isoformat()})
                    unit_total += value
                    await self.record(stock, -needed, 'kit-' + action + '-component', key + ':' + str(_),
                        {'kitSku': row.sku, 'consumedLayers': used, 'costOfGoodsSoldCents': value if action == 'sale' else 0})
                total += unit_total
                if action == 'assemble':
                    expiries = [c['expirationDate'] for c in manifest if c['expirationDate']]
                    batch = {'quantity': 1, 'costCents': unit_total, 'lotNumber': 'kit:' + key,
                        'expirationDate': min(expiries) if expiries else None, 'receivedAt': datetime.now(timezone.utc).isoformat()}
                    created = await self.receive_batches(output, [batch])
                    await self.session.flush()
                    self.session.add(KitBuildModel(tenant_id=self.tenant, kit_id=row.id, layer_id=created[0], components=manifest))
                    await self.record(output, 1, 'kit-assemble', key + ':' + str(_), {'carryingValueCents': unit_total})
        validate_total_value(total)
        return {'id': row.id, 'kitSku': row.sku, 'action': action, 'quantity': quantity,
            'costOfGoodsSoldCents': total if action == 'sale' else 0, 'carryingValueCents': total,
            'restoredComponents': restored, 'referenceId': data['referenceId']}
