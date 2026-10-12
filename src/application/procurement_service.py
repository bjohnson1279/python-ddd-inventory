import copy
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.application.costing_service import locked_config
from src.application.stock_service import StockService, StockConflict
from src.domain.product.layer_costing import validate_total_value, utc
from src.domain.replenishment import suggest
from src.infrastructure.procurement_models import (
    SupplierModel as Supplier, PurchaseOrderModel as PO, ProcurementActionModel as Action,
    ReorderPolicyModel as Policy, ASNModel,
)
from src.infrastructure.product.models import ProductModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.messaging.models import OutboxEventModel

OPEN = ('SENT', 'ACKNOWLEDGED', 'SHIPPED', 'PARTIALLY_RECEIVED')


class ProcurementNotFound(ValueError):
    pass


def supplier_dto(row):
    return {'id': row.id, 'name': row.name, 'email': row.email, 'leadTimeDays': row.lead_time_days,
            'isActive': row.is_active, 'version': row.version}


def po_dto(row):
    items = copy.deepcopy(row.items)
    return {'id': row.id, 'tenantId': row.tenant_id, 'supplier': row.supplier_name,
            'supplierId': row.supplier_id, 'locationId': row.location_id, 'status': row.status.lower(),
            'version': row.version, 'currency': 'USD', 'items': items, 'createdAt': utc(row.created_at).isoformat(),
            'expectedDeliveryDate': utc(row.expected_delivery_date).isoformat() if row.expected_delivery_date else None,
            'totalCostCents': sum(i['quantity'] * i['unitCostCents'] for i in items),
            'outstandingQuantity': 0 if row.status == 'CANCELLED' else sum(i['quantity'] - i['receivedQuantity'] for i in items)}


def policy_dto(row):
    return {'id': row.id, 'sku': row.sku, 'locationId': row.location_id, 'supplierId': row.supplier_id,
            'reorderPoint': row.reorder_point, 'targetStock': row.target_stock, 'safetyStock': row.safety_stock,
            'economicOrderQuantity': max(1, row.target_stock - row.reorder_point - row.safety_stock),
            'leadTimeDays': row.lead_time_days, 'casePack': row.case_pack,
            'minimumOrderQuantity': row.minimum_order, 'unitCostCents': row.unit_cost_cents, 'version': row.version}


class ProcurementService:
    def __init__(self, session, tenant, actor, supplier_id=None):
        self.session, self.tenant, self.actor, self.supplier_id = session, tenant, actor, supplier_id

    async def supplier(self, supplier_id, active=True):
        row = await self.session.scalar(select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == self.tenant))
        if not row or (active and not row.is_active):
            raise ProcurementNotFound('Supplier not found or inactive')
        return row

    async def po(self, po_id):
        query = select(PO).where(PO.id == po_id, PO.tenant_id == self.tenant)
        if self.supplier_id:
            await self.supplier(self.supplier_id)
            query = query.where(PO.supplier_id == self.supplier_id, PO.status.in_((*OPEN, 'RECEIVED')))
        row = await self.session.scalar(query)
        if not row:
            raise ProcurementNotFound('Purchase order not found')
        return row

    async def execute(self, operation, data, mutation_id):
        fingerprint = {'operation': operation, 'supplierIdBinding': self.supplier_id, **data}
        self.emit_event = True
        try:
            self.config = await locked_config(self.session, self.tenant)
            if self.supplier_id:
                await self.supplier(self.supplier_id)
            if data.get('po_id'):
                await self.po(data['po_id'])
            prior = await self.session.scalar(select(Action).where(Action.tenant_id == self.tenant,
                                                                  Action.mutation_id == mutation_id))
            if prior:
                if prior.request != fingerprint or prior.actor_id != self.actor:
                    raise StockConflict('Mutation ID was already used for another request')
                result = prior.result
                await self.session.rollback()
                return result
            result = await getattr(self, operation)(data)
            self.session.add(Action(tenant_id=self.tenant, mutation_id=mutation_id,
                actor_id=self.actor, request=fingerprint, result=result))
            if self.emit_event:
                self.session.add(OutboxEventModel(aggregate_type='Procurement', aggregate_id=result.get('id', self.tenant),
                    event_type='ProcurementChanged', payload={'tenantId': self.tenant, 'actorId': self.actor,
                        'mutationId': mutation_id, 'operation': operation, 'result': result}))
            await self.session.commit()
            return result
        except IntegrityError as exc:
            await self.session.rollback()
            raise StockConflict('Concurrent procurement change; retry with the same mutation ID') from exc
        except Exception:
            await self.session.rollback()
            raise

    async def create_supplier(self, data):
        row = Supplier(id=str(uuid.uuid4()), tenant_id=self.tenant, name=data['name'].strip(), email=data['email'],
                       lead_time_days=data['leadTimeDays'], is_active=True, version=0)
        self.session.add(row)
        return supplier_dto(row)

    async def update_supplier(self, data):
        row = await self.supplier(data['supplier_id'], active=False)
        if row.version != data['expectedVersion']:
            raise StockConflict('Supplier version conflict')
        for source, target in [('name', 'name'), ('email', 'email'),
                               ('leadTimeDays', 'lead_time_days'), ('isActive', 'is_active')]:
            if source in data:
                setattr(row, target, data[source].strip() if source == 'name' else data[source])
        row.version += 1
        return supplier_dto(row)

    async def validate_skus(self, items):
        skus = [i['sku'] for i in items]
        if len(skus) != len(set(skus)):
            raise ValueError('Each SKU must occur once')
        owned = set(await self.session.scalars(select(ProductModel.sku).where(
            ProductModel.tenant_id == self.tenant, ProductModel.sku.in_(skus))))
        if owned != set(skus):
            raise ProcurementNotFound('Product not found')

    async def create_po(self, data):
        supplier = await self.supplier(data['supplierId'])
        await self.validate_skus(data['items'])
        validate_total_value(sum(i['quantity'] * i['unitCostCents'] for i in data['items']))
        row = PO(id=str(uuid.uuid4()), tenant_id=self.tenant, supplier_id=supplier.id,
            supplier_name=supplier.name, location_id=data['locationId'], status='DRAFT', version=0,
            items=[{**i, 'receivedQuantity': 0} for i in data['items']], created_by=self.actor,
            created_at=datetime.now(timezone.utc), expected_delivery_date=data.get('expectedDeliveryDate'))
        if isinstance(row.expected_delivery_date, str):
            row.expected_delivery_date = datetime.fromisoformat(row.expected_delivery_date)
        self.session.add(row)
        return po_dto(row)

    async def transition(self, data):
        row = await self.po(data['po_id'])
        if data.get('expectedVersion') is not None and row.version != data['expectedVersion']:
            raise StockConflict('Purchase order version conflict')
        target = data['target']
        if row.status == target:
            self.emit_event = False
            return po_dto(row)
        if target == 'APPROVED' and row.status != 'DRAFT':
            raise StockConflict('Only draft purchase orders can be approved')
        if target == 'SENT' and row.status != 'APPROVED':
            raise StockConflict('Only approved purchase orders can be sent')
        if target == 'CANCELLED' and row.status in ('RECEIVED', 'CANCELLED'):
            raise StockConflict('Completed purchase order cannot be cancelled')
        if target in ('APPROVED', 'SENT'):
            await self.supplier(row.supplier_id)
        row.status, row.version = target, row.version + 1
        return po_dto(row)

    async def receive(self, data):
        row = await self.po(data['po_id'])
        if row.status not in OPEN:
            raise StockConflict('Purchase order is not open for receiving')
        if data.get('expectedVersion') is not None and row.version != data['expectedVersion']:
            raise StockConflict('Purchase order version conflict')
        skus = [i['sku'] for i in data['items']]
        if len(skus) != len(set(skus)):
            raise ValueError('Each received SKU must occur once')
        items = copy.deepcopy(row.items)
        ordered = {i['sku']: i for i in items}
        for receipt in data['items']:
            item = ordered.get(receipt['sku'])
            if not item or receipt['quantity'] > item['quantity'] - item['receivedQuantity']:
                raise StockConflict('Unknown SKU or receipt exceeds outstanding ordered quantity')
        results = []
        for receipt in data['items']:
            item = ordered[receipt['sku']]
            result = await StockService(self.session, self.tenant, self.actor).mutate('receive',
                receipt['sku'], row.location_id, receipt['quantity'],
                mutation_id='po-receipt:' + data['receiptId'] + ':' + row.id + ':' + receipt['sku'],
                unit_cost_cents=item['unitCostCents'], lot_number=receipt.get('lotNumber'),
                expiration_date=datetime.fromisoformat(receipt['expirationDate']) if receipt.get('expirationDate') else None,
                quarantined=receipt.get('quarantined', False), commit=False, config=self.config,
                reference={'purchaseOrderId': row.id, 'receiptId': data['receiptId']})
            item['receivedQuantity'] += receipt['quantity']
            results.append(result)
        row.items = items
        row.status = 'RECEIVED' if all(i['receivedQuantity'] == i['quantity'] for i in items) else 'PARTIALLY_RECEIVED'
        row.version += 1
        row.received_at = datetime.now(timezone.utc)
        return {**po_dto(row), 'receiptId': data['receiptId'], 'stockResults': results}

    async def save_policy(self, data):
        supplier = await self.supplier(data['supplierId'])
        await self.validate_skus([{'sku': data['sku']}])
        row = await self.session.scalar(select(Policy).where(Policy.tenant_id == self.tenant,
            Policy.sku == data['sku'], Policy.location_id == data['locationId']))
        if row and data['expectedVersion'] != row.version:
            raise StockConflict('Reorder policy version conflict')
        if not row and data['expectedVersion'] is not None:
            raise StockConflict('Reorder policy no longer exists')
        if not row:
            row = Policy(id=str(uuid.uuid4()), tenant_id=self.tenant, sku=data['sku'],
                         location_id=data['locationId'], version=0)
            self.session.add(row)
        else:
            row.version += 1
        row.supplier_id = supplier.id
        row.reorder_point, row.safety_stock = data['reorderPoint'], data['safetyStock']
        row.target_stock = data.get('targetStock') if data.get('targetStock') is not None else (
            row.reorder_point + row.safety_stock + data['economicOrderQuantity'])
        if row.target_stock < row.reorder_point or row.target_stock > 2_147_483_647:
            raise ValueError('Target stock must be at least reorder point and within stock limits')
        row.lead_time_days = data['leadTimeDays'] if data['leadTimeDays'] is not None else supplier.lead_time_days
        row.case_pack, row.minimum_order, row.unit_cost_cents = data['casePack'], data['minimumOrderQuantity'], data['unitCostCents']
        return policy_dto(row)

    async def suggestions(self, policy_id=None):
        query = select(Policy).where(Policy.tenant_id == self.tenant)
        if policy_id:
            query = query.where(Policy.id == policy_id)
        policies = list(await self.session.scalars(query.order_by(Policy.sku, Policy.location_id)))
        if policy_id and not policies:
            raise ProcurementNotFound('Reorder policy not found')
        stocks = {(s.sku, s.location_id): s for s in await self.session.scalars(select(StockModel).where(StockModel.tenant_id == self.tenant))}
        layers = list(await self.session.scalars(select(CostLayerModel).where(CostLayerModel.tenant_id == self.tenant, CostLayerModel.remaining_quantity > 0)))
        incoming = {}
        for order in await self.session.scalars(select(PO).where(PO.tenant_id == self.tenant, PO.status.in_(OPEN))):
            for item in order.items:
                key = (item['sku'], order.location_id)
                incoming[key] = incoming.get(key, 0) + item['quantity'] - item['receivedQuantity']
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        sold = {}
        for movement in await self.session.scalars(select(StockMovementModel).where(
            StockMovementModel.tenant_id == self.tenant, StockMovementModel.operation.in_(('dispatch', 'fulfill-allocation')),
            StockMovementModel.occurred_at >= cutoff)):
            key = (movement.sku, movement.location_id)
            sold[key] = sold.get(key, 0) + movement.amount
        suppliers = {s.id: s for s in await self.session.scalars(select(Supplier).where(Supplier.tenant_id == self.tenant))}
        unusable = {}
        now = datetime.now(timezone.utc)
        for layer in layers:
            if layer.quarantined or (layer.expiration_date and utc(layer.expiration_date) <= now):
                key = (layer.sku, layer.location_id)
                unusable[key] = unusable.get(key, 0) + layer.remaining_quantity
        results = []
        for policy in policies:
            key = (policy.sku, policy.location_id)
            stock = stocks.get(key)
            available = max(0, stock.quantity - stock.allocated - unusable.get(key, 0)) if stock else 0
            suggestion = suggest(available, incoming.get(key, 0), sold.get(key, 0), policy.reorder_point,
                policy.target_stock, policy.lead_time_days, policy.safety_stock, policy.case_pack, policy.minimum_order)
            active = policy.supplier_id in suppliers and suppliers[policy.supplier_id].is_active
            results.append({**policy_dto(policy), **suggestion, 'supplierActive': active,
                'unusableQuantity': unusable.get(key, 0), 'stockVersion': stock.version if stock else None,
                'reason': 'Projected usable stock at/below reorder point' if suggestion['suggestedQuantity'] else 'Sufficient stock including incoming orders'})
        return results

    async def draft_from_policy(self, data):
        item = (await self.suggestions(data['policy_id']))[0]
        if data['expectedVersion'] != item['version']:
            raise StockConflict('Reorder policy version conflict')
        if not item['suggestedQuantity'] or item['unitCostCents'] is None:
            raise StockConflict('No order needed, or policy purchase cost is unknown')
        return await self.create_po({'supplierId': item['supplierId'], 'locationId': item['locationId'],
            'items': [{'sku': item['sku'], 'quantity': item['suggestedQuantity'], 'unitCostCents': item['unitCostCents']}]})

    async def acknowledge(self, data):
        row = await self.po(data['po_id'])
        if row.status not in ('SENT', 'ACKNOWLEDGED'):
            raise StockConflict('Purchase order cannot be acknowledged in its current state')
        row.expected_delivery_date = datetime.fromisoformat(data['expected_delivery_date'])
        row.status, row.version = 'ACKNOWLEDGED', row.version + 1
        return {'success': True, **po_dto(row)}

    async def asn(self, data):
        row = await self.po(data['po_id'])
        if row.status not in OPEN:
            raise StockConflict('Purchase order is not open for shipping')
        ordered = {i['sku']: i for i in row.items}
        totals = {}
        for prior in await self.session.scalars(select(ASNModel).where(ASNModel.tenant_id == self.tenant, ASNModel.po_id == row.id)):
            for item in prior.payload['items']:
                totals[item['sku']] = totals.get(item['sku'], 0) + item['shipped_quantity']
        skus = [i['sku'] for i in data['items']]
        if len(skus) != len(set(skus)):
            raise ValueError('Each shipped SKU must occur once')
        for item in data['items']:
            line = ordered.get(item['sku'])
            if not line or totals.get(item['sku'], 0) + item['shipped_quantity'] > line['quantity'] or (
                    item['shipped_quantity'] > line['quantity'] - line['receivedQuantity']):
                raise StockConflict('Shipment exceeds outstanding purchase order quantity')
        asn = ASNModel(id=str(uuid.uuid4()), tenant_id=self.tenant, supplier_id=row.supplier_id, po_id=row.id, payload=data)
        self.session.add(asn)
        if row.status != 'PARTIALLY_RECEIVED':
            row.status = 'SHIPPED'
        row.version += 1
        return {'success': True, 'asn_id': asn.id, 'status': row.status.lower()}
