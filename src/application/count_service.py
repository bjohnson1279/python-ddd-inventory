"""Reviewed physical counts and stock adjustments in one inventory transaction."""
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.application.costing_service import locked_config, apply_costing, load_layers
from src.application.stock_service import StockConflict, stock_dto
from src.domain.product.layer_costing import consume
from src.infrastructure.cycle_count.models import (
    CycleCountRecordModel as Record, CycleCountLineItemModel as Line,
    CycleCountPlanModel as Plan, CountRequestModel,
)
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.auth.identity import UserModel


class CountNotFound(ValueError):
    pass


class CountService:
    def __init__(self, session, tenant, actor, manager=True):
        self.session, self.tenant, self.actor, self.manager = session, tenant, actor, manager

    async def record(self, record_id):
        row = await self.session.scalar(select(Record).where(Record.id == record_id,
            Record.tenant_id == self.tenant))
        if not row or (not self.manager and row.assigned_operator_id != self.actor):
            raise CountNotFound('Count not found')
        return row

    async def lines(self, record):
        return list(await self.session.scalars(select(Line).where(Line.record_id == record.id).order_by(Line.sku)))

    async def stock(self, sku, location):
        return await self.session.scalar(select(StockModel).where(StockModel.tenant_id == self.tenant,
            StockModel.sku == sku, StockModel.location_id == location))

    async def execute(self, operation, data, mutation_id=None):
        mutation_id = mutation_id or str(uuid.uuid4())
        fingerprint = {'operation': operation, 'actorId': self.actor, **data}
        try:
            self.config = await locked_config(self.session, self.tenant)
            prior = await self.session.scalar(select(CountRequestModel).where(
                CountRequestModel.tenant_id == self.tenant, CountRequestModel.mutation_id == mutation_id))
            # Recheck assignment before returning a submission replay.
            if data.get('record_id'):
                await self.record(data['record_id'])
            if prior:
                if prior.request != fingerprint:
                    raise StockConflict('Mutation ID was already used for another request')
                result = prior.result
                await self.session.rollback()
                return result
            result = await getattr(self, operation)(data, mutation_id)
            self.session.add(CountRequestModel(tenant_id=self.tenant, mutation_id=mutation_id,
                                               request=fingerprint, result=result))
            await self.session.commit()
            return result
        except IntegrityError as exc:
            await self.session.rollback()
            raise StockConflict('Concurrent count mutation; retry with the same mutation ID') from exc
        except Exception:
            await self.session.rollback()
            raise

    async def snapshot(self, record, skus=None):
        query = select(StockModel).where(StockModel.tenant_id == self.tenant,
                                       StockModel.location_id == record.location_id)
        if skus:
            query = query.where(StockModel.sku.in_(skus))
        stocks = list(await self.session.scalars(query.order_by(StockModel.sku).limit(1001)))
        if len(stocks) > 1000:
            raise ValueError('Limit each count to 1000 SKUs using an explicit SKU selection')
        if not stocks or (skus and {s.sku for s in stocks} != set(skus)):
            raise ValueError('Every count SKU must have stock at the selected location')
        for stock in stocks:
            self.session.add(Line(record_id=record.id, sku=stock.sku, expected_quantity=stock.quantity,
                                  stock_version=stock.version, status='PENDING'))

    async def start(self, data, mutation_id):
        assigned = data.get('assigned_operator_id') or self.actor
        user = await self.session.scalar(select(UserModel).where(UserModel.id == assigned,
            UserModel.tenant_id == self.tenant, UserModel.is_active.is_(True)))
        if not user or user.role not in ('ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'):
            raise ValueError('Count assignee must be an active count operator in this tenant')
        record = Record(id=str(uuid.uuid4()), tenant_id=self.tenant, name=data['name'],
            location_id=data['location_id'], zone=data['location_id'], assigned_operator_id=assigned,
            status='PENDING', abc_classification='A', is_blind_count=data['is_blind_count'], revision=0)
        self.session.add(record)
        await self.snapshot(record, data.get('skus'))
        await self.session.flush()
        return await self.dto(record)

    async def dto(self, record):
        masked = record.is_blind_count and not self.manager
        return {'record_id': record.id, 'name': record.name, 'status': record.status,
            'location_id': record.location_id, 'zone': record.zone, 'revision': record.revision,
            'assigned_operator_id': record.assigned_operator_id, 'is_blind_count': record.is_blind_count,
            'approved_by': record.approved_by,
            'items': [{'sku': line.sku, 'expected_quantity': None if masked else line.expected_quantity,
                'counted_quantity': line.counted_quantity, 'variance_quantity': None if masked else line.variance_quantity,
                'status': line.status} for line in await self.lines(record)]}

    async def submit(self, data, mutation_id):
        record = await self.record(data['record_id'])
        if record.status not in ('PENDING', 'RECOUNT_REQUIRED'):
            raise StockConflict('Count is not open for submission')
        if (record.revision > 0 and data.get('revision') is None) or (
                data.get('revision') is not None and record.revision != data['revision']):
            raise StockConflict('Stale count revision')
        lines = await self.lines(record)
        observations = {item['sku']: item['counted_quantity'] for item in data['items']}
        if not lines or len(observations) != len(data['items']) or set(observations) != {line.sku for line in lines}:
            raise ValueError('Submit every count line exactly once')
        flagged = False
        for line in lines:
            line.counted_quantity = observations[line.sku]
            line.variance_quantity = line.counted_quantity - line.expected_quantity
            large = abs(line.variance_quantity) > line.expected_quantity * .05
            line.status = 'VARIANCE_FLAGGED' if large else 'MATCHED'
            flagged |= large
        record.status = 'SUBMITTED'
        await self.session.flush()
        return {'success': True, 'status': record.status, 'requiresReview': True,
                'varianceFlagged': flagged, 'record_id': record.id, 'revision': record.revision}

    async def recount(self, data, mutation_id):
        record = await self.record(data['record_id'])
        if record.status == 'COMPLETED' or record.revision != data['revision']:
            raise StockConflict('Count is completed or its revision is stale')
        location = record.location_id or data.get('location_id')
        if not location:
            raise ValueError('Legacy counts require an explicit location for recount')
        lines = await self.lines(record)
        for line in lines:
            stock = await self.stock(line.sku, location)
            if not stock:
                raise ValueError('Stock not found at count location')
            line.expected_quantity, line.stock_version = stock.quantity, stock.version
            line.counted_quantity = line.variance_quantity = line.variance_value = None
            line.status = 'PENDING'
        if not lines:
            raise ValueError('Count has no persisted lines')
        record.location_id = location
        record.revision += 1
        record.status = 'RECOUNT_REQUIRED'
        await self.session.flush()
        return await self.dto(record)

    async def approve(self, data, mutation_id):
        record = await self.record(data['record_id'])
        if record.revision != data['revision']:
            raise StockConflict('Stale count revision')
        if record.status == 'COMPLETED':
            if not record.approval_result:
                raise StockConflict('Legacy completed count has no reconciliation result')
            return record.approval_result
        if record.status != 'SUBMITTED':
            raise StockConflict('Count must be submitted before approval')
        lines = await self.lines(record)
        if not record.location_id or not lines:
            raise StockConflict('Legacy counts need a fresh stock snapshot through recount')
        adjustments = []
        for line in lines:
            stock = await self.stock(line.sku, record.location_id)
            if not stock or line.stock_version is None or stock.version != line.stock_version:
                raise StockConflict('Stock changed since count snapshot; recount before approval')
            if line.counted_quantity is None:
                raise StockConflict('Count has missing observations')
            if line.variance_quantity:
                result = await self.adjust_stock(stock, line.variance_quantity, 'COUNT',
                    data['reason'], f'count:{record.id}:{record.revision}:{line.id}', record.id)
                adjustments.append(result)
                line.variance_value = result['inventoryValueChangeCents']
            else:
                line.variance_value = 0
            line.status = 'RECONCILED'
        record.status, record.approved_by = 'COMPLETED', self.actor
        record.approval_result = {'success': True, 'status': 'COMPLETED', 'record_id': record.id,
            'revision': record.revision, 'approvedBy': self.actor, 'adjustments': adjustments}
        return record.approval_result

    async def adjust_stock(self, stock, delta, code, reason, mutation_id, count_id=None, unit_cost=None, lot=None):
        quantity = stock.quantity + delta
        if quantity < stock.allocated:
            raise StockConflict('Adjustment would remove reserved stock; resolve reservations first')
        if not 0 <= quantity <= 2_147_483_647:
            raise ValueError('Adjusted quantity is outside supported limits')
        version = stock.version
        if delta > 0:
            costing = await apply_costing(self.session, self.tenant, stock, 'receive', delta,
                                          self.config.costing_method, unit_cost, lot)
            value = delta * unit_cost if unit_cost is not None else None
        else:
            layers = await load_layers(self.session, self.tenant, stock)
            cost, used = consume(layers, -delta, self.config.costing_method,
                                  lot_number=lot, include_ineligible=True)
            value = -cost if cost is not None else None
            costing = {'consumedLayers': used, 'costingMethod': self.config.costing_method,
                       'costStatus': 'PRICED' if cost is not None else 'UNPRICED'}
        changed = await self.session.execute(update(StockModel).where(StockModel.id == stock.id,
            StockModel.tenant_id == self.tenant, StockModel.version == version).values(quantity=quantity, version=version + 1))
        if changed.rowcount != 1:
            raise StockConflict('Stock version conflict')
        result = {**stock_dto(stock), 'quantity': quantity, 'available': quantity - stock.allocated,
            'version': version + 1, 'delta': delta, 'inventoryValueChangeCents': value,
            'reasonCode': code, 'reason': reason, 'countId': count_id, **costing}
        self.session.add(StockMovementModel(tenant_id=self.tenant, mutation_id=mutation_id,
            actor_id=self.actor, sku=stock.sku, location_id=stock.location_id, operation='adjust',
            amount=delta, request={'reasonCode': code, 'reason': reason, 'countId': count_id}, result=result))
        self.session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=stock.id,
            event_type='StockAdjusted', payload={**result, 'tenantId': self.tenant, 'actorId': self.actor,
                                                'mutationId': mutation_id}))
        await self.session.flush()
        return result

    async def adjust(self, data, mutation_id):
        stock = await self.stock(data['sku'], data['location_id'])
        if not stock:
            raise CountNotFound('Stock not found at location')
        if stock.version != data['expected_version']:
            raise StockConflict('Stock version conflict')
        return await self.adjust_stock(stock, data['delta'], data['reason_code'], data['reason'],
                                      'adjust:' + mutation_id, unit_cost=data.get('unit_cost_cents'), lot=data.get('lot_number'))

    async def schedule(self, data, mutation_id):
        plans = list(await self.session.scalars(select(Plan).where(Plan.tenant_id == self.tenant, Plan.is_active.is_(True))))
        audits = []
        for plan in plans:
            latest = await self.session.scalar(select(Record).where(Record.tenant_id == self.tenant,
                Record.plan_id == plan.id).order_by(Record.created_at.desc()).limit(1))
            if latest:
                date = latest.created_at.replace(tzinfo=timezone.utc) if latest.created_at.tzinfo is None else latest.created_at
                if latest.status != 'COMPLETED' or datetime.now(timezone.utc) - date < timedelta(days=plan.frequency_days):
                    continue
            location = plan.zone or 'default'
            if not await self.session.scalar(select(StockModel.id).where(StockModel.tenant_id == self.tenant,
                                                                       StockModel.location_id == location).limit(1)):
                continue
            record = Record(id=str(uuid.uuid4()), tenant_id=self.tenant, plan_id=plan.id, name=plan.name,
                location_id=location, zone=location, assigned_operator_id=self.actor, status='PENDING',
                abc_classification=plan.abc_classification, is_blind_count=True, revision=0)
            self.session.add(record)
            await self.snapshot(record)
            await self.session.flush()
            audits.append(await self.dto(record))
        return {'scheduled': len(audits), 'audits': audits}

    async def sync(self, data, mutation_id):
        results = []
        for submission in data['submissions']:
            await self.record(submission['record_id'])
            key = submission['mutation_id']
            payload = {k: v for k, v in submission.items() if k != 'mutation_id'}
            fingerprint = {'operation': 'submit', 'actorId': self.actor, **payload}
            prior = await self.session.scalar(select(CountRequestModel).where(
                CountRequestModel.tenant_id == self.tenant, CountRequestModel.mutation_id == key))
            if prior:
                if prior.request != fingerprint:
                    raise StockConflict('Mutation ID was already used for another request')
                results.append(prior.result)
            else:
                result = await self.submit(payload, key)
                self.session.add(CountRequestModel(tenant_id=self.tenant, mutation_id=key,
                                                   request=fingerprint, result=result))
                await self.session.flush()
                results.append(result)
        return {'synced': True, 'results': results}
