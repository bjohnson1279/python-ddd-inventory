from sqlalchemy import select
from src.infrastructure.transfer_models import UomConfigModel, TransferModel
from src.infrastructure.product.models import ProductModel
from src.infrastructure.stock_models import StockModel
from src.infrastructure.procurement_models import PurchaseOrderModel, ReorderPolicyModel
from src.application.stock_service import StockConflict
from src.domain.uom import STANDARD_UNITS, factor, base_quantity, decimal_fraction


def uom_dto(row):
    return {'id': row.id, 'sku': row.sku, 'baseUnit': row.base_unit, 'category': row.category,
            'purchaseUnit': row.purchase_unit, 'saleUnit': row.sale_unit,
            'rules': row.rules, 'version': row.version}


async def owned_product(session, tenant, sku):
    product = await session.scalar(select(ProductModel).where(ProductModel.tenant_id == tenant, ProductModel.sku == sku))
    if not product:
        raise LookupError('Product not found')
    return product


async def configured(session, tenant, sku):
    return await session.scalar(select(UomConfigModel).where(UomConfigModel.tenant_id == tenant, UomConfigModel.sku == sku))


async def convert_input(session, tenant, sku, amount, unit, expected_version):
    row = await configured(session, tenant, sku)
    if not row:
        raise StockConflict('Configure product units before sending a unit')
    if expected_version is None or row.version != expected_version:
        raise StockConflict('UOM configuration version conflict')
    return base_quantity(row, amount, unit), {'unit': unit, 'quantity': str(amount),
        'baseUnit': row.base_unit, 'baseQuantity': base_quantity(row, amount, unit), 'uomVersion': row.version}


async def save_uom(session, tenant, data):
    await owned_product(session, tenant, data['sku'])
    row = await configured(session, tenant, data['sku'])
    if data['expectedVersion'] != (row.version if row else 0):
        raise StockConflict('UOM configuration version conflict')
    if row and (row.base_unit != data['baseUnit'] or row.category != data['category']):
        raise StockConflict('Base unit and category are immutable; migrate the product explicitly')
    if not row and data['baseUnit'] != 'ea':
        stock = await session.scalar(select(StockModel.id).where(StockModel.tenant_id == tenant, StockModel.sku == data['sku']).limit(1))
        orders = await session.scalars(select(PurchaseOrderModel).where(PurchaseOrderModel.tenant_id == tenant))
        ordered = any(any(i['sku'] == data['sku'] for i in p.items) for p in orders)
        transfers = await session.scalars(select(TransferModel).where(TransferModel.tenant_id == tenant))
        transferred = any(any(i['sku'] == data['sku'] for i in transfer.items) for transfer in transfers)
        policy = await session.scalar(select(ReorderPolicyModel.id).where(ReorderPolicyModel.tenant_id == tenant, ReorderPolicyModel.sku == data['sku']).limit(1))
        if stock or ordered or policy or transferred:
            raise StockConflict('Existing inventory/procurement history uses eaches; cannot reinterpret its base unit')
    units = [rule['unit'] for rule in data['rules']]
    if len(units) != len(set(units)) or data['baseUnit'] in units:
        raise ValueError('Conversion rules must have unique non-base units')
    if data['category'] in STANDARD_UNITS and data['rules']:
        raise ValueError('Standard weight/volume factors cannot be overridden')
    for rule in data['rules']:
        if not 0 < decimal_fraction(rule['factorToBase']) <= 1_000_000:
            raise ValueError('Conversion factors must be positive and at most 1000000')
    if not row:
        row = UomConfigModel(tenant_id=tenant, sku=data['sku'], version=0)
        session.add(row)
    row.base_unit, row.category, row.rules = data['baseUnit'], data['category'], data['rules']
    row.purchase_unit, row.sale_unit = data['purchaseUnit'] or row.base_unit, data['saleUnit'] or row.base_unit
    factor(row, row.base_unit)
    factor(row, row.purchase_unit)
    factor(row, row.sale_unit)
    row.version += 1
    await session.flush()
    return uom_dto(row)
