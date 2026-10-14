import uuid
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.product.models import ProductModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.application.costing_service import locked_config, apply_costing
from src.domain.product.layer_costing import utc


class StockConflict(ValueError):
    pass


def stock_dto(stock):
    return {'id': stock.id, 'sku': stock.sku, 'locationId': stock.location_id,
            'quantity': stock.quantity, 'allocated': stock.allocated,
            'available': stock.quantity - stock.allocated, 'version': stock.version}


class StockService:
    def __init__(self, session, tenant_id, actor_id):
        self.session, self.tenant_id, self.actor_id = session, tenant_id, actor_id

    async def mutate(self, operation, sku, location, amount, mutation_id=None, expected_version=None,
                     unit_cost_cents=None, lot_number=None, expiration_date=None,
                     received_at=None, quarantined=False, *, commit=True, config=None, reference=None,
                     unit=None, uom_version=None):
        if not unit and (type(amount) is not int or amount <= 0):
            raise ValueError('Quantity must be a positive integer')
        if unit_cost_cents is not None and (type(unit_cost_cents) is not int or not 0 <= unit_cost_cents <= 2_147_483_647):
            raise ValueError('Unit cost must be nonnegative integer cents within the supported limit')
        if operation != 'receive' and (unit_cost_cents is not None or expiration_date is not None or received_at is not None or quarantined):
            raise ValueError('Receipt costing metadata is only supported for receiving')
        if lot_number is not None and operation not in ('receive', 'dispatch', 'fulfill-allocation'):
            raise ValueError('Lot selection is only supported for receiving or dispatching')
        fingerprint = {'operation': operation, 'sku': sku, 'locationId': location,
                       'amount': amount, 'expectedVersion': expected_version}
        if unit:
            fingerprint.update(unit=unit, uomVersion=uom_version)
        metadata = {'unitCostCents': unit_cost_cents, 'lotNumber': lot_number,
                    'expirationDate': utc(expiration_date).isoformat() if expiration_date else None,
                    'receivedAt': utc(received_at).isoformat() if received_at else None}
        fingerprint.update({key: value for key, value in metadata.items() if value is not None})
        if quarantined:
            fingerprint['quarantined'] = True
        if reference:
            fingerprint['reference'] = reference
        mutation_id = mutation_id or str(uuid.uuid4())
        try:
            config = config or await locked_config(self.session, self.tenant_id)
            prior = await self.session.scalar(select(StockMovementModel).where(
                StockMovementModel.tenant_id == self.tenant_id,
                StockMovementModel.mutation_id == mutation_id))
            if prior:
                if prior.request != fingerprint:
                    raise StockConflict('Mutation ID was already used for another request')
                result = prior.result
                if commit:
                    await self.session.rollback()
                return result
            conversion, receipt_total = None, None
            if unit:
                from src.application.uom_service import convert_input
                from src.domain.uom import receipt_value
                original_amount = amount
                amount, conversion = await convert_input(self.session, self.tenant_id, sku, amount, unit, uom_version)
                if operation == 'receive' and unit_cost_cents is not None:
                    receipt_total = receipt_value(original_amount, unit_cost_cents)
                    unit_cost_cents = None  # Price is per input unit, not per ledger unit.
            product = await self.session.scalar(select(ProductModel.id).where(
                ProductModel.tenant_id == self.tenant_id, ProductModel.sku == sku))
            if not product:
                raise ValueError('Product not found')
            row = await self.session.scalar(select(StockModel).where(
                StockModel.tenant_id == self.tenant_id, StockModel.sku == sku,
                StockModel.location_id == location))
            if not row:
                if operation != 'receive':
                    raise ValueError('Stock not found at location')
                row = StockModel(id=str(uuid.uuid4()), tenant_id=self.tenant_id,
                                 sku=sku, location_id=location, quantity=0, allocated=0, version=0)
                self.session.add(row)
                await self.session.flush()
            version = row.version
            if expected_version is not None and expected_version != version:
                raise StockConflict('Stock version conflict')
            quantity, allocated = row.quantity, row.allocated
            if operation == 'receive':
                quantity += amount
            elif operation == 'dispatch':
                if quantity - allocated < amount:
                    raise ValueError('Insufficient available stock')
                quantity -= amount
            elif operation == 'allocate':
                if quantity - allocated < amount:
                    raise ValueError('Insufficient available stock')
                allocated += amount
            elif operation == 'release-allocation':
                if allocated < amount:
                    raise ValueError('Insufficient allocated stock')
                allocated -= amount
            elif operation == 'fulfill-allocation':
                if allocated < amount:
                    raise ValueError('Insufficient allocated stock')
                quantity -= amount
                allocated -= amount
            else:
                raise ValueError('Unsupported stock operation')
            if quantity > 2_147_483_647:
                raise ValueError('Quantity exceeds supported stock limit')
            costing = await apply_costing(self.session, self.tenant_id, row, operation, amount,
                config.costing_method, unit_cost_cents, lot_number, expiration_date, received_at, quarantined,
                receipt_value_cents=receipt_total)
            changed = await self.session.execute(update(StockModel).where(
                StockModel.id == row.id, StockModel.tenant_id == self.tenant_id,
                StockModel.version == version).values(quantity=quantity, allocated=allocated,
                                                      version=version + 1))
            if changed.rowcount != 1:
                raise StockConflict('Stock version conflict')
            result = {'id': row.id, 'sku': sku, 'locationId': location,
                      'quantity': quantity, 'allocated': allocated,
                      'available': quantity - allocated, 'version': version + 1, **costing}
            if reference:
                result['reference'] = reference
            if conversion:
                result['inputUom'] = conversion
                if operation == 'receive':
                    result['receiptValueCents'] = receipt_total
            self.session.add(StockMovementModel(tenant_id=self.tenant_id, mutation_id=mutation_id,
                actor_id=self.actor_id, sku=sku, location_id=location, operation=operation,
                amount=amount, request=fingerprint, result=result))
            self.session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=row.id,
                event_type='StockChanged', payload={**result, 'tenantId': self.tenant_id,
                    'actorId': self.actor_id, 'mutationId': mutation_id, 'operation': operation}))
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            return result
        except IntegrityError as exc:
            if commit:
                await self.session.rollback()
            raise StockConflict('Concurrent stock mutation; retry with the same mutation ID') from exc
        except Exception:
            if commit:
                await self.session.rollback()
            raise
